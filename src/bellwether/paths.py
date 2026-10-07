"""Resolve writable runtime data separately from installed Python code."""
from __future__ import annotations

import os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]


def runtime_root() -> Path:
    configured = os.environ.get("BENCHTREND_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    # Source checkouts keep their existing data and browser routes. The
    # pipeline package src/icml exists only in a checkout — the wheel ships
    # bellwether and benchtrend alone — so it identifies one.
    if (CODE_ROOT / "pyproject.toml").is_file() and (CODE_ROOT / "src" / "icml").is_dir():
        return CODE_ROOT
    home = Path.home() / ".benchtrend"
    legacy = Path.home() / ".bellwether"
    if not home.exists() and (legacy / "data/processed/benchmark_snapshot.json").is_file():
        return legacy
    return home


ROOT = runtime_root()


def agent_environment() -> dict[str, str]:
    # The MCP child must use the parent's data even when launched elsewhere.
    env = {"BENCHTREND_HOME": str(ROOT)}
    if (CODE_ROOT / "src/bellwether").is_dir():
        env["PYTHONPATH"] = str(CODE_ROOT / "src")
    return env
