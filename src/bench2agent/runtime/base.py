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
                 messages: list | None = None):
        self.text = text
        self.trail = trail
        self.provider_session = provider_session
        self.usage = usage or {}
        self.messages = messages           # only connectors that keep their own history fill this

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


def read_tool_log(path: Path) -> list[dict]:
    """The MCP server's record of this run: every call with its result."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


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
