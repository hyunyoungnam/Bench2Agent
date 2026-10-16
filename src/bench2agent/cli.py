"""Bench2Agent terminal conversations and a local MCP service."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, providers
from .session import Session, conversations, write_json

HELP = "/new  /chats  /resume ID  /sources  /status  /help  /exit"
TOOL_LABELS = {"benchmark_scope": "Checking data coverage", "benchmark_usage": "Counting benchmark use",
               "benchmark_trend": "Comparing editions", "new_benchmarks": "Finding introductions",
               "benchmark_adoption": "Counting adoption", "benchmark_evidence": "Checking paper evidence"}


def safe_text(text) -> str:
    # Model text, paper titles and saved questions can contain terminal controls.
    # Keep newlines/tabs and printable Unicode, remove escape/control sequences.
    text = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", str(text))
    return "".join(c for c in text if c in "\n\t" or (c.isprintable() and c != "\x7f"))


def render(turn: dict, *, sources: bool = False) -> str:
    pieces, evidence = [], []
    for seg in turn.get("segs", []):
        if seg.get("t") == "p":
            pieces.append(seg.get("s", ""))
        elif seg.get("t") == "n":
            mark = ((" [unverified date]" if seg.get("kind") == "date" else " [unverified figure]")
                    if seg.get("v") in {"no", "na", "unconnected"} else "")
            pieces.append(seg.get("s", "") + mark)
        elif seg.get("t") == "c":
            evidence.append(seg)
            pieces.append(f"[{len(evidence)}]" + (" [unverified quote]" if not seg.get("v") else ""))
    if sources:
        for i, seg in enumerate(evidence, 1):
            verdict = "✓" if seg.get("v") else "✗"
            pieces.append(f"\n[{i}] {verdict} {seg.get('title', '')}\n    {seg.get('url') or seg.get('paper_id') or seg.get('gid')}\n    {seg.get('q', '')}\n")
    else:
        for i, seg in enumerate(evidence, 1):
            pieces.append(f"\n[{i}] {seg.get('title', '')} · {seg.get('url') or seg.get('paper_id') or seg.get('gid')}")
    checked = turn.get("verified") or {}
    if turn.get("unverifiable"):
        pieces.append("\n\nAnswer could not be verified: " + str(turn["unverifiable"]) +
                      ". This turn is kept for display and excluded from follow-up context.")
    if checked.get("checked") or checked.get("fchecked"):
        pieces.append(f"\n\nQuotes {checked.get('passed', 0)}/{checked.get('checked', 0)} · "
                      f"Figures {checked.get('fpassed', 0)}/{checked.get('fchecked', 0)}")
    return safe_text("".join(pieces))


def settings_path(root: Path) -> Path:
    return root / "data/bench2agent/settings.json"


def settings(root: Path) -> dict:
    try:
        with settings_path(root).open(encoding="utf-8") as source:
            value = json.load(source)
        if not isinstance(value, dict):
            raise ValueError("Invalid settings. Run bench2agent init.")
        return value
    except FileNotFoundError:
        return {}


def configuration(args, root: Path, *, interactive: bool = False) -> dict:
    saved = settings(root)
    provider = args.provider or saved.get("provider")
    if not provider:
        provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("OPENAI_API_KEY") else "openai"
        if interactive and not any(os.environ.get(name) for name in providers.KEY_NAMES.values()):
            choice = input("Model provider [openai / anthropic]: ").strip().lower()
            provider = choice or provider
    if provider not in providers.KEY_NAMES:
        raise ValueError("Choose --provider openai or anthropic.")
    model = args.model or (saved.get("model") if saved.get("provider") == provider else None) or providers.DEFAULT_MODELS[provider]
    language = args.language or saved.get("language", "auto")
    return {"provider": provider, "model": model, "language": language}


def ensure_key(config: dict, *, interactive: bool):
    name = providers.KEY_NAMES[config["provider"]]
    if not os.environ.get(name):
        if not interactive:
            raise ValueError(f"Set {name} or start bench2agent interactively to enter a key for this session.")
        key = getpass.getpass(name + " (this session only): ").strip()
        if not key:
            raise ValueError("API key is required.")
        os.environ[name] = key


def cmd_init(args, root: Path) -> int:
    config = configuration(args, root, interactive=sys.stdin.isatty())
    if sys.stdin.isatty() and not args.model:
        config["model"] = input(f"Model [{config['model']}]: ").strip() or config["model"]
    write_json(settings_path(root), config)
    print(f"Saved {config['provider']} / {config['model']} · data: {root}")
    print(f"Set {providers.KEY_NAMES[config['provider']]} in your environment, or enter it when bench2agent starts.")
    print("Next: bench2agent")
    return 0


CLIENT_LABELS = {"claude": "Claude Code", "codex": "Codex"}


def client_status(run=subprocess.run) -> dict:
    """Is Bench2Agent reachable through Claude Code and Codex?

    Asked of the clients' own CLIs (`<client> mcp get bench2agent`), never read
    from their config or credential files. `signed_in` comes from the same
    login probes the browser settings use; None means the probe gave no answer.
    """
    from bench2agent.core.chat import _agent_status
    out = {}
    for name in ("claude", "codex"):
        info = _agent_status(name)
        entry = {"installed": info["installed"], "signed_in": info["authenticated"],
                 "mcp_registered": None, "mcp_scope": None, "mcp_connected": None}
        if info["installed"]:
            try:
                done = run([name, "mcp", "get", "bench2agent"], capture_output=True, text=True,
                           timeout=20, stdin=subprocess.DEVNULL)
                entry["mcp_registered"] = done.returncode == 0
                if name == "claude" and done.returncode == 0:
                    scope = re.search(r"Scope:\s*(\w+)", done.stdout or "")
                    state = re.search(r"Status:\s*\S*\s*(\w+)", done.stdout or "")
                    entry["mcp_scope"] = scope.group(1).lower() if scope else None
                    entry["mcp_connected"] = (state.group(1).lower() == "connected") if state else None
            except (OSError, subprocess.TimeoutExpired):
                pass
        out[name] = entry
    return out


def _client_line(name: str, c: dict) -> str:
    label = CLIENT_LABELS[name]
    if not c["installed"]:
        return f"{label}: not installed"
    parts = ["installed",
             {True: "signed in", False: "not signed in", None: "sign-in unknown"}[c["signed_in"]]]
    if c["mcp_registered"]:
        where = f" ({c['mcp_scope']} scope)" if c["mcp_scope"] else ""
        parts.append("bench2agent MCP server registered" + where)
        if c["mcp_connected"] is True:
            parts.append("connected")
        elif c["mcp_connected"] is False:
            parts.append("NOT connecting — check `bench2agent data status` and the registered command")
    elif c["mcp_registered"] is False:
        parts.append(f"bench2agent MCP server not registered → run: bench2agent mcp --connect {name}")
    else:
        parts.append("MCP registration unknown")
    return f"{label}: " + " · ".join(parts)


def cmd_status(args, root: Path) -> int:
    from .data import status
    from bench2agent.runtime import available_connectors
    result = {"version": __version__, "home": str(root), "data": status(root),
              "settings": settings(root), "api_keys_present": {
                  provider: bool(os.environ.get(name)) for provider, name in providers.KEY_NAMES.items()},
              "connectors": [{k: c.get(k) for k in ("name", "label", "installed", "connected", "account", "detail")}
                             for c in available_connectors()],
              "clients": client_status()}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"Bench2Agent {__version__} · {root}")
        print(json.dumps(result["data"], ensure_ascii=False, indent=2))
        print("Ways to answer (Bench2Agent is installed separately from the official CLIs):")
        for c in result["connectors"]:
            state = ("connected" + (f" · {c['account']}" if c.get("account") else "")) if c["connected"] else (
                "installed, not signed in" if c.get("installed") else "not installed")
            print(f"  --connector {c['name']:6} {c['label']}: {state}")
        keys = ", ".join(f"{p}: {'set' if v else 'not set'}" for p, v in result["api_keys_present"].items())
        print("Standalone conversation (`bench2agent`): uses your signed-in Claude Code or Codex.")
        print(f"Optional --connector api: separate API billing — {keys}")
        print("Through an AI client (no API key; uses the client's own login):")
        for name, c in result["clients"].items():
            print("  " + _client_line(name, c))
    return 0


def cmd_data(args, root: Path) -> int:
    from . import data
    if args.action == "status":
        result = data.status(root)
    elif args.action == "install":
        result = data.install(root, file=args.file, url=args.url, sha256=args.sha256, activate=not args.no_activate)
    elif args.action == "sqlite":
        result = data.build_sqlite(root, args.source, args.out)
    elif args.action == "activate":
        result = data.activate(root, args.revision)
    else:
        result = data.bundle(root, Path(args.out))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_migrate(args, root: Path) -> int:
    from .migration import migrate
    print(json.dumps(migrate(root, Path(args.source)), ensure_ascii=False, indent=2))
    return 0


def print_chats(root: Path):
    rows = conversations(root)
    for row in rows:
        print(safe_text(f"{row['id']}  {row['provider']}  {row['title']}"))
    if not rows:
        print("No saved conversations.")


def load_session(args, root: Path, *, interactive: bool) -> Session:
    cid = args.resume
    if cid == "latest":
        rows = conversations(root)
        if not rows:
            raise ValueError("No saved conversations to resume.")
        cid = rows[0]["id"]
    if cid:
        # The saved conversation owns its connector; resuming never probes or
        # prompts for a different provider before loading it.
        config = configuration(args, root)
        connector = "api"  # constructor default only; Session.load replaces it
    else:
        connector = getattr(args, "connector", None) or saved_connector(root) or default_connector(interactive)
        if connector == "api":
            config = configuration(args, root, interactive=interactive)
            if interactive and not settings(root) and not args.model:
                config["model"] = input(f"Model [{config['model']}]: ").strip() or config["model"]
        else:
            saved = settings(root)
            config = {"provider": "anthropic" if connector == "claude" else "openai",
                      "model": args.model or (saved.get("model") if saved.get("connector") == connector else None)
                      or (providers.DEFAULT_MODELS["openai"] if connector == "codex" else None),
                      "language": args.language or saved.get("language", "auto")}
        if interactive:
            write_json(settings_path(root), {**config, "connector": connector})
    session = Session(root, **config, cid=cid, connector=connector)
    if cid and ((args.provider and args.provider != session.doc["provider"]) or
                (args.model and args.model != session.doc["model"])):
        raise ValueError("A resumed conversation keeps its provider and model. Start a new conversation to change them.")
    return session


def saved_connector(root: Path) -> str | None:
    return (settings(root) or {}).get("connector")


def default_connector(interactive: bool) -> str:
    """No API key by default: the official CLIs already signed in on this
    machine come first. An interactive run looks for them and asks when more
    than one is connected; a scripted run (`ask`, piped input) does not probe
    the CLIs and takes `--connector` or the saved choice, else the API key."""
    if not interactive:
        return "api"
    from bench2agent.runtime import available_connectors
    connected = [c for c in available_connectors() if c["name"] in ("claude", "codex") and c.get("connected")]
    if not connected:
        raise ValueError("No signed-in Claude Code or Codex was found. Install an official CLI separately, "
                         "then run `claude auth login` or `codex login` and restart bench2agent. "
                         "To use your own API key instead, explicitly select `bench2agent --connector api`.")
    if len(connected) == 1:
        return connected[0]["name"]
    names = [c["name"] for c in connected]
    print("Signed-in agent CLIs on this machine: " + ", ".join(f"{c['name']} ({c.get('account') or 'signed in'})" for c in connected),
          file=sys.stderr)
    choice = input(f"Answer with [{names[0]}]: ").strip() or names[0]
    return choice if choice in names else names[0]


def require_data(root: Path, *, interactive: bool):
    from .data import status
    current = status(root)
    if current["available"]:
        return
    if current.get("pointer") != "none" or current.get("revisions"):
        # something is installed but cannot be served: a broken pointer, a
        # damaged revision, or an unreadable database — never download over it
        raise ValueError(current.get("reason") or "Installed data cannot be served; run bench2agent data status.")
    if interactive:
        from .data import install
        print("Downloading benchmark data; checking its checksum…", file=sys.stderr, flush=True)
        install(root)
        return
    raise ValueError("Benchmark data is missing. Run bench2agent data install.")


def cmd_chat(args, root: Path) -> int:
    interactive = sys.stdin.isatty() and not getattr(args, "question", None)
    require_data(root, interactive=interactive)
    session = load_session(args, root, interactive=interactive)
    if session.doc.get("connector", "api") == "api":        # the CLI connectors bring their own login
        ensure_key(session.doc, interactive=interactive)
    last = session.doc["turns"][-1] if session.doc["turns"] else None

    def emit(event):
        if event["t"] == "tool":
            print(safe_text("  " + TOOL_LABELS.get(event["name"], event["name"])), file=sys.stderr, flush=True)

    def answer(question):
        nonlocal last
        last = session.ask(question, emit)
        if args.json:
            print(json.dumps({"chat": session.doc["id"], **last}, ensure_ascii=False))
        else:
            print("\n" + render(last) + "\n", flush=True)

    if getattr(args, "question", None):
        answer(args.question)
        return 0
    if not args.json:
        from bench2agent.core.mcp import B
        snapshot = B.data()
        editions = ", ".join(sorted(snapshot["editions"]))
        name = session.doc.get("connector", "api")
        engine = session.doc['provider'] if name == "api" else CLIENT_LABELS[name]
        print(f"Bench2Agent · {engine} / {session.doc['model'] or 'CLI default'} · {session.doc['id']}")
        print(safe_text("Data: " + editions))
        print(HELP + "\n")
        if last:
            print(render(last) + "\n")
    if interactive:
        try:
            import readline  # optional on Windows; do not save questions outside our chat store
        except ImportError:
            pass
    failed = False
    while True:
        try:
            question = input("> " if interactive else "").strip()
        except EOFError:
            break
        except KeyboardInterrupt:
            if interactive:
                print("\n" + HELP)
                continue
            return 130
        if not question:
            continue
        if question in {"/exit", "/quit"}:
            break
        if question == "/help":
            print(HELP)
            continue
        if question == "/chats":
            print_chats(root)
            continue
        if question == "/status":
            cmd_status(args, root)
            continue
        if question == "/sources":
            print(render(last, sources=True) if last else "No answer yet.")
            continue
        if question == "/new":
            session = Session(root, session.doc["provider"], session.doc["model"], language=session.doc.get("language", "auto"),
                              connector=session.doc.get("connector", "api"))
            last = None
            print(f"New conversation · {session.doc['id']} · {session.doc['connector']}")
            continue
        try:
            if question.startswith("/resume "):
                cid = question.split(maxsplit=1)[1]
                candidate = Session(root, session.doc["provider"], session.doc["model"], cid=cid)
                if candidate.doc.get("connector", "api") == "api":
                    ensure_key(candidate.doc, interactive=interactive)
                session = candidate
                last = session.doc["turns"][-1] if session.doc["turns"] else None
                print("Resumed · " + session.doc["id"])
                if last:
                    print(render(last))
            elif question.startswith("/"):
                print(HELP)
            else:
                answer(question)
        except KeyboardInterrupt:
            session._drop_provider_session()      # the CLI's own session is not resumed after a cancel
            print("\nCancelled; the unfinished turn was not saved.", file=sys.stderr)
            if not interactive:
                return 130
        except (ValueError, OSError, providers.ProviderError) as exc:
            if str(exc) == "cancelled":                       # Ctrl+C reached the CLI connector mid-answer
                print("\nCancelled; the unfinished turn was not saved.", file=sys.stderr)
                if not interactive:
                    return 130
                continue
            failed = True
            print(safe_text(str(exc)), file=sys.stderr)
            if not interactive:
                break
    if not args.json and session.doc["turns"]:
        print("Continue: bench2agent --resume " + session.doc["id"])
    return 1 if failed else 0


def mcp_command(root: Path) -> list[str]:
    # An absolute Python/module command survives venv installations and does
    # not depend on bench2agent being discoverable in the MCP host's PATH.
    return [sys.executable, "-m", "bench2agent", "--home", str(root), "mcp"]


def client_command(client: str, root: Path, *, model: str | None = None) -> list[str]:
    executable = shutil.which(client)
    if not executable:
        raise ValueError(f"{CLIENT_LABELS[client]} is not installed or is not on PATH. Install it first, then run bench2agent {client}.")
    from bench2agent.core.paths import agent_environment
    env = {**agent_environment(), "BENCH2AGENT_HOME": str(root)}
    command = mcp_command(root)
    if client == "claude":
        config = {"mcpServers": {"bench2agent": {"command": command[0], "args": command[1:], "env": env}}}
        native = [executable, "--mcp-config", json.dumps(config)]
        if model:
            native += ["--model", model]
        return native
    # Codex's -c values are TOML; JSON-quoted strings and arrays also fit TOML.
    settings = {"command": json.dumps(command[0]), "args": json.dumps(command[1:]),
                "env": "{" + ", ".join(json.dumps(k) + "=" + json.dumps(v) for k, v in env.items()) + "}"}
    native = [executable]
    for key, value in settings.items():
        native += ["-c", f"mcp_servers.bench2agent.{key}={value}"]
    native += ["--model", model or providers.DEFAULT_MODELS["openai"]]
    return native


def cmd_client(args, root: Path) -> int:
    command = client_command(args.command, root, model=getattr(args, "model", None))
    if args.dry_run:
        print(shlex.join(command))
        return 0
    require_data(root, interactive=True)
    print(f"Opening {CLIENT_LABELS[args.command]} with Bench2Agent…", file=sys.stderr, flush=True)
    return subprocess.run(command, check=False).returncode


def cmd_mcp(args, root: Path) -> int:
    command = mcp_command(root)
    if args.config:
        from bench2agent.core.paths import agent_environment
        print(json.dumps({"mcpServers": {"bench2agent": {"command": command[0], "args": command[1:],
                                                      "env": agent_environment()}}}, indent=2))
        return 0
    if args.connect:
        client = shutil.which(args.connect)
        if not client:
            raise ValueError(f"{args.connect} is not installed or is not on PATH.")
        from bench2agent.core.paths import agent_environment
        env = agent_environment()
        if args.connect == "codex":
            native = [client, "mcp", "add", "bench2agent"]
            for key, value in env.items():
                native += ["--env", key + "=" + value]
        else:
            native = [client, "mcp", "add"]
            for key, value in env.items():
                native += ["--env", key + "=" + value]
            # Claude's --env is variadic; another option must terminate it
            # before the positional server name.
            native += ["--transport", "stdio", "--scope", "user", "bench2agent"]
        native += ["--", *command]
        if args.dry_run:
            print(shlex.join(native))
            return 0
        result = subprocess.run(native, check=False)
        if not result.returncode:
            print(f"Connected Bench2Agent to {args.connect}. Start a new {args.connect} session to use it.")
        return result.returncode
    if args.dry_run:
        raise ValueError("--dry-run requires --connect codex or --connect claude.")
    if getattr(args, "http", False):
        try:
            from bench2agent.core import mcp_http
        except ImportError:
            raise ValueError("The HTTP server needs the server extra: pip install 'bench2agent[server]'") from None
        return mcp_http.serve(root, host=args.host, port=args.port, revision=getattr(args, "revision", None))
    from bench2agent.core import mcp as server
    from bench2agent.core.benchmarks import BenchmarkStore
    # the served revision is fixed for this process: the one named, else the
    # one active now; new data needs a restart
    server.B = BenchmarkStore(root, revision=getattr(args, "revision", None))
    if server.B.unavailable or server.B.revision is None:
        raise ValueError(server.B.unavailable or "No data revision is installed. Run bench2agent data install.")
    return server.serve_stdio(tools=server._benchmark_tools(), name="bench2agent")


def common_options(parser, *, child=False):
    default = argparse.SUPPRESS if child else None
    parser.add_argument("--home", default=default, help="runtime data directory (or BENCH2AGENT_HOME)")
    parser.add_argument("--provider", choices=list(providers.KEY_NAMES), default=default)
    parser.add_argument("--model", default=default, help="model ID supported by your account")
    parser.add_argument("--language", choices=["auto", "ko", "en"], default=default)
    parser.add_argument("--resume", nargs="?", const="latest", default=default, metavar="ID")
    parser.add_argument("--connector", choices=["api", "claude", "codex"], default=default,
                        help="how the question runs: a provider API key (default), or the official Claude Code / Codex CLI with its own login")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS if child else False)


def cmd_web(args, root: Path) -> int:
    """The hosted conversation (needs the server extra) and its user tokens."""
    if args.web_action == "serve":
        try:
            from bench2agent.web import app as web_app
        except ImportError:
            raise ValueError("The web host needs the server extra: pip install 'bench2agent[server]'") from None
        return web_app.serve(root, host=args.host, port=args.port)
    from bench2agent.web.store import WebStore
    store = WebStore(root / "data/bench2agent/web.sqlite")
    if args.web_action == "invite":
        if args.invite_action == "add":
            result = store.add_invite(args.label, uses=args.uses, days=args.days or None)
            print(json.dumps({k: result[k] for k in ("label", "uses", "expires_at")}, ensure_ascii=False))
            print(f"Invite code (shown once; the user enters it on the page): {result['code']}", file=sys.stderr)
            return 0
        if args.invite_action == "revoke":
            print(json.dumps({"revoked": store.revoke_invite(args.label)}))
            return 0
        print(json.dumps(store.invites(), ensure_ascii=False, indent=2))
        return 0
    if args.web_action == "attempts":
        print(json.dumps(store.attempts(args.user, args.limit), ensure_ascii=False, indent=2))
        return 0
    if args.web_action == "user":
        if args.user_action == "add":
            result = store.add_user(args.name)
            print(json.dumps({k: result[k] for k in ("user_id", "name")}, ensure_ascii=False))
            print(f"Token (shown once; give it to {args.name}): {result['token']}", file=sys.stderr)
            return 0
        if args.user_action == "token":
            result = store.add_token(args.name)
            print(f"Token (shown once): {result['token']}", file=sys.stderr)
            return 0
        if args.user_action == "revoke":
            print(json.dumps(store.revoke_user(args.name)))
            return 0
        print(json.dumps(store.users(), ensure_ascii=False, indent=2))
    return 0


def cmd_service(args, root: Path) -> int:
    """Use the same service implementation from the public terminal command."""
    from .core import cli as service
    handlers = {"serve": service.cmd_serve, "setup": service.cmd_setup,
                "bundle": service.cmd_bundle, "fetch-data": service.cmd_fetch,
                "datasets": service.cmd_datasets, "benchmarks": service.cmd_benchmarks}
    return handlers[args.command](args)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bench2agent", description=__doc__)
    parser.add_argument("--version", action="version", version="Bench2Agent " + __version__)
    common_options(parser)
    commands = parser.add_subparsers(dest="command")
    chat = commands.add_parser("chat", help="start a conversation (also the default)")
    common_options(chat, child=True)
    chat.set_defaults(fn=cmd_chat)
    ask = commands.add_parser("ask", help="answer one question")
    ask.add_argument("question")
    common_options(ask, child=True)
    ask.set_defaults(fn=cmd_chat)
    init = commands.add_parser("init", help="choose model provider and model")
    common_options(init, child=True)
    init.set_defaults(fn=cmd_init)
    status = commands.add_parser("status", help="data and model connection status")
    common_options(status, child=True)
    status.set_defaults(fn=cmd_status)
    chats = commands.add_parser("chats", help="list saved conversations")
    common_options(chats, child=True)
    chats.set_defaults(fn=lambda args, root: print_chats(root) or 0)
    migration = commands.add_parser("migrate", help="copy data and saved chats from a previous installation")
    common_options(migration, child=True)
    migration.add_argument("--from", dest="source", required=True, metavar="DIR", help="previous runtime data directory")
    migration.set_defaults(fn=cmd_migrate)
    data = commands.add_parser("data", help="install, inspect or bundle benchmark data")
    common_options(data, child=True)
    actions = data.add_subparsers(dest="action", required=True)
    data_status = actions.add_parser("status")
    common_options(data_status, child=True)
    install = actions.add_parser("install")
    common_options(install, child=True)
    source = install.add_mutually_exclusive_group()
    source.add_argument("--file")
    source.add_argument("--url")
    install.add_argument("--sha256", help="expected SHA-256 of the downloaded file")
    install.add_argument("--no-activate", action="store_true",
                         help="install and verify the revision as a candidate; the active pointer stays where it is")
    bundle = actions.add_parser("bundle")
    common_options(bundle, child=True)
    bundle.add_argument("--out", required=True)
    sqlite = actions.add_parser("sqlite", help="convert a legacy snapshot JSON (data format v1) into a database file; never into a revision")
    common_options(sqlite, child=True)
    sqlite.add_argument("source", help="path of a benchmark_snapshot.json")
    sqlite.add_argument("--out", required=True)
    activate = actions.add_parser("activate", help="serve an already installed data revision (rollback)")
    common_options(activate, child=True)
    activate.add_argument("revision")
    data.set_defaults(fn=cmd_data)
    mcp = commands.add_parser("mcp", help="serve read-only tools or connect an existing AI client")
    common_options(mcp, child=True)
    mode = mcp.add_mutually_exclusive_group()
    mode.add_argument("--config", action="store_true", help="print MCP configuration for other clients")
    mode.add_argument("--connect", choices=["codex", "claude"], help="register via the client's own CLI")
    mcp.add_argument("--dry-run", action="store_true", help="show registration command without changing settings")
    mcp.add_argument("--revision", help="serve this installed data revision instead of the active one (reproduce earlier answers)")
    mcp.add_argument("--http", action="store_true", help="serve over Streamable HTTP (needs the server extra) instead of stdio")
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8765)
    mcp.set_defaults(fn=cmd_mcp)
    for name in ("claude", "codex"):
        client = commands.add_parser(name, help=f"open {CLIENT_LABELS[name]} with Bench2Agent tools")
        common_options(client, child=True)
        client.add_argument("--dry-run", action="store_true", help="show the launch command without downloading data or starting the client")
        client.set_defaults(fn=cmd_client)
    serve = commands.add_parser("serve", help="start the local web interface")
    common_options(serve, child=True)
    serve.add_argument("--port", type=int, default=8001)
    serve.add_argument("--no-meili", action="store_true", help="do not start the optional search engine")
    serve.set_defaults(fn=cmd_service)
    for name, description in (("setup", "install the optional browser search engine"),
                              ("bundle", "bundle the full browser data on the build machine")):
        service = commands.add_parser(name, help=description)
        common_options(service, child=True)
        service.set_defaults(fn=cmd_service)
    fetch = commands.add_parser("fetch-data", help="install a full browser data bundle")
    common_options(fetch, child=True)
    source = fetch.add_mutually_exclusive_group()
    source.add_argument("--file")
    source.add_argument("--url")
    source.add_argument("--release", metavar="TAG")
    fetch.set_defaults(fn=cmd_service)
    datasets = commands.add_parser("datasets", help="export paper and dataset source inventories")
    common_options(datasets, child=True)
    datasets.add_argument("--out", default=".")
    datasets.add_argument("--gid", type=int)
    datasets.set_defaults(fn=cmd_service)
    web = commands.add_parser("web", help="hosted conversation over HTTP MCP (server extra) and its users")
    common_options(web, child=True)
    web_actions = web.add_subparsers(dest="web_action", required=True)
    web_serve = web_actions.add_parser("serve")
    common_options(web_serve, child=True)
    web_serve.add_argument("--host", default="127.0.0.1")
    web_serve.add_argument("--port", type=int, default=8780)
    web_user = web_actions.add_parser("user", help="add, list, token, revoke")
    common_options(web_user, child=True)
    user_actions = web_user.add_subparsers(dest="user_action", required=True)
    for name in ("add", "token", "revoke"):
        sub = user_actions.add_parser(name)
        common_options(sub, child=True)
        sub.add_argument("name")
    common_options(user_actions.add_parser("list"), child=True)
    invite = web_actions.add_parser("invite", help="invite codes: add, list, revoke (a web user signs in with one)")
    common_options(invite, child=True)
    invite_actions = invite.add_subparsers(dest="invite_action", required=True)
    invite_add = invite_actions.add_parser("add")
    common_options(invite_add, child=True)
    invite_add.add_argument("label", help="who or what this invite is for")
    invite_add.add_argument("--uses", type=int, default=1)
    invite_add.add_argument("--days", type=int, default=14, help="validity; 0 for no expiry")
    invite_revoke = invite_actions.add_parser("revoke")
    common_options(invite_revoke, child=True)
    invite_revoke.add_argument("label")
    common_options(invite_actions.add_parser("list"), child=True)
    attempts = web_actions.add_parser("attempts", help="recent questions: outcome, duration, tool calls (no credentials, no answer text)")
    common_options(attempts, child=True)
    attempts.add_argument("--user")
    attempts.add_argument("--limit", type=int, default=50)
    web.set_defaults(fn=cmd_web)
    benchmarks = commands.add_parser("benchmarks", help="build the benchmark revision database from reviewed extraction")
    common_options(benchmarks, child=True)
    benchmarks.add_argument("--out")
    benchmarks.add_argument("--publish", action="store_true",
                            help="also install the built database as the new active data revision")
    benchmarks.set_defaults(fn=cmd_service)
    args = parser.parse_args(argv)
    if args.home:
        os.environ["BENCH2AGENT_HOME"] = args.home
    from bench2agent.core.paths import ROOT
    try:
        return getattr(args, "fn", cmd_chat)(args, ROOT)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, providers.ProviderError) as exc:
        print(safe_text(str(exc)), file=sys.stderr)
        return 1
