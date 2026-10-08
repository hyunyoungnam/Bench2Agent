"""Resolve writable runtime data separately from installed Python code."""
from __future__ import annotations

import os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[3]


def runtime_root() -> Path:
    configured = os.environ.get("BENCH2AGENT_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    # Source checkouts keep their existing data and browser routes. The
    # pipeline package src/icml exists only in a checkout — the wheel ships
    # bench2agent alone — so it identifies one.
    if (CODE_ROOT / "pyproject.toml").is_file() and (CODE_ROOT / "src" / "icml").is_dir():
        return CODE_ROOT
    return Path.home() / ".bench2agent"


ROOT = runtime_root()


def agent_environment() -> dict[str, str]:
    # The MCP child must use the parent's data even when launched elsewhere.
    env = {"BENCH2AGENT_HOME": str(ROOT)}
    if (CODE_ROOT / "src/bench2agent/core").is_dir():
        env["PYTHONPATH"] = str(CODE_ROOT / "src")
    return env
