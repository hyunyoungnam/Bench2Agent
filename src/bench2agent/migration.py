"""Copy a previous installation's research data and conversations without overwriting."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .data import MEMBER, install, validate
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
    from .core import revisions
    # revisions of the previous install come across whole; a previous
    # legacy snapshot (data/processed/) is registered as a revision once
    previous_revisions = revisions.list_revisions(source)
    for rev in previous_revisions:
        src_dir = Path(rev["path"])
        if (revisions.revision_dir(root, rev["id"]) / "manifest.json").is_file():
            continue
        file = src_dir / "benchmark_snapshot.json" if rev["json"] else src_dir / "benchmark_snapshot.sqlite"
        install(root, file=str(file), activate=False)
        result["snapshot_copied"] = True
    active = next((r["id"] for r in previous_revisions if r["active"]), None)
    if active and revisions.active_revision(root) is None:
        revisions.set_active(root, active)
    snapshot = source / MEMBER
    if snapshot.is_file() and not previous_revisions:
        validate(snapshot)
        installed = install(root, file=str(snapshot), activate=revisions.active_revision(root) is None)
        result["snapshot_copied"] = installed["outcome"] != "reused"
        result["revision"] = installed["revision"]
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
