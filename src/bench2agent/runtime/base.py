"""The connector contract, and the trail every connector must produce."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


class ConnectorError(RuntimeError):
    pass


class Run:
    """What a question produced: the answer text, the tool trail as the MCP
    server recorded it (name, arguments, result, revision), the provider's
    own conversation id for the next turn, and usage when known."""

    def __init__(self, text: str, trail: list[dict], provider_session: str | None = None, usage: dict | None = None,
                 messages: list | None = None, unverifiable: str | None = None):
        self.text = text
        self.trail = trail
        self.provider_session = provider_session
        self.usage = usage or {}
        self.messages = messages           # only connectors that keep their own history fill this
        # set when the trail cannot stand for what the model read: no log
        # although tools were called, a damaged log, a call on another
        # revision, fewer logged calls than the CLI showed. Such a turn is
        # saved as unverifiable, never with verification marks.
        self.unverifiable = unverifiable

    def trail_for_turn(self) -> list[dict]:
        """The shape the verifier and the saved turn use."""
        return [{"name": t["name"], "arg": json.dumps(t.get("arguments") or {}, sort_keys=True, ensure_ascii=False)}
                for t in self.trail]


class Connector:
    """One way to run a question. Subclasses implement `status`, `login` and
    `ask`; `disconnect` where the connector holds anything."""
    name = "connector"
    label = "connector"
    owns_loop = False      # True when the official CLI runs the model/tool loop

    def status(self) -> dict:
        """{"connected": bool, "account": str|None, "how": str, "public_hosting": str}
        `how` says what the connection is (which login, where it lives);
        `public_hosting` says what is known about offering this on a remote
        service, from the provider's own documents."""
        raise NotImplementedError

    def login(self) -> dict:
        """How to connect: {"kind": "command"|"url"|"none", ...}. Connectors
        never collect credentials; they point at the provider's own flow."""
        raise NotImplementedError

    def ask(self, *, root: Path, revision: str, question: str, system: str, history, provider_session: str | None,
            emit, cancelled, tool_log: Path) -> Run:
        raise NotImplementedError

    def disconnect(self) -> dict:
        return {"disconnected": False, "how": "Sign out in the provider's own tool."}


def mcp_stdio_command(root: Path, revision: str) -> list[str]:
    """The pinned stdio MCP server an agent CLI is given: Bench2Agent's own,
    on this revision, with the tool log turned on by the environment."""
    return [sys.executable, "-m", "bench2agent", "--home", str(root), "mcp", "--revision", revision]


def mcp_environment(root: Path, tool_log: Path) -> dict:
    env = {"BENCH2AGENT_HOME": str(root), "BENCH2AGENT_TOOL_LOG": str(tool_log)}
    code_root = Path(__file__).resolve().parents[3]
    if (code_root / "src/bench2agent/core").is_dir():
        env["PYTHONPATH"] = str(code_root / "src")
    return env


def read_tool_log(path: Path) -> tuple[list[dict], str | None]:
    """The MCP server's record of this run: every call with its result, and
    the reason it cannot be trusted, if any (damaged lines)."""
    if not path.exists():
        return [], None
    out, damaged = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict) or "name" not in entry or "result" not in entry:
                    raise ValueError("shape")
                out.append(entry)
            except ValueError:
                damaged += 1
    return out, (f"{damaged} damaged entries in the tool log" if damaged else None)


def check_trail(log: list[dict], damaged: str | None, seen: list[dict], revision: str) -> str | None:
    """Why this turn cannot be verified, or None when the log stands for
    what the model read: every logged call on the requested revision, and at
    least as many logged calls as the CLI showed."""
    if damaged:
        return damaged
    if seen and not log:
        return "the CLI called tools but the MCP server left no log"
    if len(log) < len(seen):
        return f"the MCP log has {len(log)} calls but the CLI showed {len(seen)}"
    wrong = sorted({str(e.get("revision")) for e in log if e.get("revision") != revision})
    if wrong:
        return f"tool calls were answered from revision {', '.join(wrong)} instead of {revision}"
    return None


def transcript(turns: list[dict] | None, limit: int = 6) -> str:
    """Earlier saved turns as plain text for a fresh provider session: used
    after a cancel, a failure or an unverifiable turn, when the CLI's own
    session is not resumed. Only turns Bench2Agent saved appear here."""
    out = []
    for t in (turns or [])[-limit:]:
        answer = "".join(s.get("s", "") for s in t.get("segs", []) if s.get("t") in ("p", "n"))
        out.append(f"User: {t.get('q', '')}\nAssistant: {answer[:2000]}")
    return ("Earlier in this conversation:\n\n" + "\n\n".join(out) + "\n\n") if out else ""


_REGISTRY: dict[str, type] = {}


def register(cls):
    _REGISTRY[cls.name] = cls
    return cls


def connector(name: str, **kwargs) -> Connector:
    if name not in _REGISTRY:
        raise ConnectorError(f"Unknown connector {name!r}; one of {sorted(_REGISTRY)}")
    return _REGISTRY[name](**kwargs)


def available_connectors(**kwargs) -> list[dict]:
    """Every connector with its status, the ones whose CLI is installed first."""
    out = []
    for name, cls in _REGISTRY.items():
        try:
            inst = cls(**{k: v for k, v in kwargs.items() if k in getattr(cls, "options", ())})
            out.append({"name": name, "label": cls.label, **inst.status()})
        except Exception as exc:  # noqa: BLE001 — a connector that cannot even report is reported as such
            out.append({"name": name, "label": cls.label, "connected": False, "how": f"status failed: {type(exc).__name__}",
                        "public_hosting": "unknown"})
    return out


def which(binary: str) -> str | None:
    return shutil.which(binary) or (binary if os.path.isabs(binary) and os.path.exists(binary) else None)
