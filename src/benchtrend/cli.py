"""BenchTrend terminal conversations and a local MCP service."""
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
            mark = " [unverified figure]" if seg.get("v") in {"no", "na"} else ""
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
    if checked.get("checked") or checked.get("fchecked"):
        pieces.append(f"\n\nQuotes {checked.get('passed', 0)}/{checked.get('checked', 0)} · "
                      f"Figures {checked.get('fpassed', 0)}/{checked.get('fchecked', 0)}")
    return safe_text("".join(pieces))


def settings_path(root: Path) -> Path:
    return root / "data/benchtrend/settings.json"


def settings(root: Path) -> dict:
    try:
        with settings_path(root).open(encoding="utf-8") as source:
            value = json.load(source)
        if not isinstance(value, dict):
            raise ValueError("Invalid settings. Run benchtrend init.")
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
            raise ValueError(f"Set {name} or start benchtrend interactively to enter a key for this session.")
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
    print(f"Set {providers.KEY_NAMES[config['provider']]} in your environment, or enter it when benchtrend starts.")
    print("Next: benchtrend")
    return 0


CLIENT_LABELS = {"claude": "Claude Code", "codex": "Codex"}


def client_status(run=subprocess.run) -> dict:
    """Is BenchTrend reachable through Claude Code and Codex?

    Asked of the clients' own CLIs (`<client> mcp get benchtrend`), never read
    from their config or credential files. `signed_in` comes from the same
    login probes the browser settings use; None means the probe gave no answer.
    """
    from bellwether.chat import _agent_status
    out = {}
    for name in ("claude", "codex"):
        info = _agent_status(name)
        entry = {"installed": info["installed"], "signed_in": info["authenticated"],
                 "mcp_registered": None, "mcp_scope": None, "mcp_connected": None}
        if info["installed"]:
            try:
                done = run([name, "mcp", "get", "benchtrend"], capture_output=True, text=True,
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
        parts.append("benchtrend MCP server registered" + where)
        if c["mcp_connected"] is True:
            parts.append("connected")
        elif c["mcp_connected"] is False:
            parts.append("NOT connecting — check `benchtrend data status` and the registered command")
    elif c["mcp_registered"] is False:
        parts.append(f"benchtrend MCP server not registered → run: benchtrend mcp --connect {name}")
    else:
        parts.append("MCP registration unknown")
    return f"{label}: " + " · ".join(parts)


def cmd_status(args, root: Path) -> int:
    from .data import status
    result = {"version": __version__, "home": str(root), "data": status(root),
              "settings": settings(root), "api_keys_present": {
                  provider: bool(os.environ.get(name)) for provider, name in providers.KEY_NAMES.items()},
              "clients": client_status()}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"BenchTrend {__version__} · {root}")
        print(json.dumps(result["data"], ensure_ascii=False, indent=2))
        keys = ", ".join(f"{p}: {'set' if v else 'not set'}" for p, v in result["api_keys_present"].items())
        print(f"Standalone conversation (`benchtrend`): needs an API key — {keys}")
        print("Through an AI client (no API key; uses the client's own login):")
        for name, c in result["clients"].items():
            print("  " + _client_line(name, c))
    return 0


def cmd_data(args, root: Path) -> int:
    from . import data
    if args.action == "status":
        result = data.status(root)
    elif args.action == "install":
        result = data.install(root, file=args.file, url=args.url, sha256=args.sha256)
    else:
        result = data.bundle(root, Path(args.out))
    print(json.dumps(result, ensure_ascii=False, indent=2))
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
    config = configuration(args, root, interactive=interactive and not cid)
    if interactive and not cid and not settings(root):
        if not args.model:
            config["model"] = input(f"Model [{config['model']}]: ").strip() or config["model"]
        write_json(settings_path(root), config)
    session = Session(root, **config, cid=cid)
    if cid and ((args.provider and args.provider != session.doc["provider"]) or
                (args.model and args.model != session.doc["model"])):
        raise ValueError("A resumed conversation keeps its provider and model. Start a new conversation to change them.")
    return session


def require_data(root: Path, *, interactive: bool):
    if (root / "data/processed/benchmark_snapshot.json").is_file():
        return
    if interactive:
        source = input("Benchmark data file or HTTPS URL: ").strip()
        if source:
            from .data import install
            print(json.dumps(install(root, **({"url": source} if source.startswith("https://") else {"file": source})), indent=2))
            return
    raise ValueError("Benchmark data is missing. Run benchtrend data install --file FILE or --url URL.")


def cmd_chat(args, root: Path) -> int:
    interactive = sys.stdin.isatty() and not getattr(args, "question", None)
    require_data(root, interactive=interactive)
    session = load_session(args, root, interactive=interactive)
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
        from bellwether.mcp import B
        snapshot = B.data()
        editions = ", ".join(sorted(snapshot["editions"]))
        print(f"BenchTrend · {session.doc['provider']} / {session.doc['model']} · {session.doc['id']}")
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
            session = Session(root, session.doc["provider"], session.doc["model"], language=session.doc.get("language", "auto"))
            last = None
            print("New conversation · " + session.doc["id"])
            continue
        try:
            if question.startswith("/resume "):
                cid = question.split(maxsplit=1)[1]
                candidate = Session(root, session.doc["provider"], session.doc["model"], cid=cid)
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
            print("\nCancelled; the unfinished turn was not saved.", file=sys.stderr)
            if not interactive:
                return 130
        except (ValueError, OSError, providers.ProviderError) as exc:
            failed = True
            print(safe_text(str(exc)), file=sys.stderr)
            if not interactive:
                break
    if not args.json and session.doc["turns"]:
        print("Continue: benchtrend --resume " + session.doc["id"])
    return 1 if failed else 0


def mcp_command(root: Path) -> list[str]:
    # An absolute Python/module command survives venv installations and does
    # not depend on benchtrend being discoverable in the MCP host's PATH.
    return [sys.executable, "-m", "benchtrend", "--home", str(root), "mcp"]


def cmd_mcp(args, root: Path) -> int:
    command = mcp_command(root)
    if args.config:
        from bellwether.paths import agent_environment
        print(json.dumps({"mcpServers": {"benchtrend": {"command": command[0], "args": command[1:],
                                                      "env": agent_environment()}}}, indent=2))
        return 0
    if args.connect:
        client = shutil.which(args.connect)
        if not client:
            raise ValueError(f"{args.connect} is not installed or is not on PATH.")
        from bellwether.paths import agent_environment
        env = agent_environment()
        if args.connect == "codex":
            native = [client, "mcp", "add", "benchtrend"]
            for key, value in env.items():
                native += ["--env", key + "=" + value]
        else:
            native = [client, "mcp", "add"]
            for key, value in env.items():
                native += ["--env", key + "=" + value]
            # Claude's --env is variadic; another option must terminate it
            # before the positional server name.
            native += ["--transport", "stdio", "--scope", "user", "benchtrend"]
        native += ["--", *command]
        if args.dry_run:
            print(shlex.join(native))
            return 0
        result = subprocess.run(native, check=False)
        if not result.returncode:
            print(f"Connected BenchTrend to {args.connect}. Start a new {args.connect} session to use it.")
        return result.returncode
    if args.dry_run:
        raise ValueError("--dry-run requires --connect codex or --connect claude.")
    from bellwether.mcp import _benchmark_tools, serve_stdio
    return serve_stdio(tools=_benchmark_tools(), name="benchtrend")


def common_options(parser, *, child=False):
    default = argparse.SUPPRESS if child else None
    parser.add_argument("--home", default=default, help="runtime data directory (or BENCHTREND_HOME)")
    parser.add_argument("--provider", choices=list(providers.KEY_NAMES), default=default)
    parser.add_argument("--model", default=default, help="model ID supported by your account")
    parser.add_argument("--language", choices=["auto", "ko", "en"], default=default)
    parser.add_argument("--resume", nargs="?", const="latest", default=default, metavar="ID")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS if child else False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="benchtrend", description=__doc__)
    parser.add_argument("--version", action="version", version="BenchTrend " + __version__)
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
    data = commands.add_parser("data", help="install, inspect or bundle benchmark data")
    common_options(data, child=True)
    actions = data.add_subparsers(dest="action", required=True)
    data_status = actions.add_parser("status")
    common_options(data_status, child=True)
    install = actions.add_parser("install")
    common_options(install, child=True)
    source = install.add_mutually_exclusive_group(required=True)
    source.add_argument("--file")
    source.add_argument("--url")
    install.add_argument("--sha256", help="expected SHA-256 of the downloaded file")
    bundle = actions.add_parser("bundle")
    common_options(bundle, child=True)
    bundle.add_argument("--out", required=True)
    data.set_defaults(fn=cmd_data)
    mcp = commands.add_parser("mcp", help="serve read-only tools or connect an existing AI client")
    common_options(mcp, child=True)
    mode = mcp.add_mutually_exclusive_group()
    mode.add_argument("--config", action="store_true", help="print MCP configuration for other clients")
    mode.add_argument("--connect", choices=["codex", "claude"], help="register via the client's own CLI")
    mcp.add_argument("--dry-run", action="store_true", help="show registration command without changing settings")
    mcp.set_defaults(fn=cmd_mcp)
    args = parser.parse_args(argv)
    if args.home:
        os.environ["BENCHTREND_HOME"] = args.home
    from bellwether.paths import ROOT
    try:
        return getattr(args, "fn", cmd_chat)(args, ROOT)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, providers.ProviderError) as exc:
        print(safe_text(str(exc)), file=sys.stderr)
        return 1
