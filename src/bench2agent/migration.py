"""Copy a previous installation's research data and conversations without overwriting."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .data import MEMBER, validate
from .session import write_json


def _copy_missing(source: Path, target: Path) -> bool:
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stage:
            pending = Path(stage.name)
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, stage)
        try:
            os.link(pending, target)
        except FileExistsError:
            return False
        return True
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def migrate(root: Path, source: Path) -> dict:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise ValueError("Previous installation directory does not exist.")
    result = {"source": str(source), "home": str(root.resolve()),
              "snapshot_copied": False, "settings_copied": False, "chats_copied": 0}
    snapshot = source / MEMBER
    if snapshot.is_file() and not (root / MEMBER).exists():
        validate(snapshot)
        result["snapshot_copied"] = _copy_missing(snapshot, root / MEMBER)
    target = root / "data/bench2agent"
    for previous in (source / "data/bench2agent", source / "data/benchtrend"):
        settings = previous / "settings.json"
        if settings.is_file() and not (target / "settings.json").exists():
            saved = json.loads(settings.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                raise ValueError("Previous settings are invalid.")
            write_json(target / "settings.json", {k: saved[k] for k in
                       ("provider", "model", "language") if k in saved})
            result["settings_copied"] = True
        for chat in sorted((previous / "chats").glob("*.json")):
            if re.fullmatch(r"[0-9a-f]{12}\.json", chat.name):
                result["chats_copied"] += _copy_missing(chat, target / "chats" / chat.name)
    return result
