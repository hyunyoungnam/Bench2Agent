"""Immutable data revisions and the one pointer that says which is active.

    <root>/data/revisions/<snapshot_id>/benchmark_snapshot.json
    <root>/data/revisions/<snapshot_id>/benchmark_snapshot.sqlite  (+ .sha256)
    <root>/data/revisions/<snapshot_id>/manifest.json
    <root>/data/revisions/active        # the snapshot_id being served

A revision directory is written completely into a staging directory, checked,
moved into place with one rename, and only then named in `active` with one
replace. Nothing that fails before that last step changes what is served,
and a revision once published is never edited: a saved conversation can keep
reading the revision it started on while new conversations get the new one.

`active` missing means the legacy layout (`data/processed/`) is served, so
existing installs and the source checkout keep working unchanged.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .repository import DATABASE, SNAPSHOT

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def revisions_dir(root: Path) -> Path:
    return root / "data/revisions"


def valid_id(rid: str) -> bool:
    return bool(rid) and bool(_ID.fullmatch(rid)) and rid not in ("active", ".", "..")


def revision_dir(root: Path, rid: str) -> Path:
    if not valid_id(rid):
        raise ValueError(f"Invalid revision id: {rid!r}")
    return revisions_dir(root) / rid


def active_revision(root: Path) -> str | None:
    pointer = revisions_dir(root) / "active"
    try:
        rid = pointer.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return rid if valid_id(rid) and revision_dir(root, rid).is_dir() else None


def list_revisions(root: Path) -> list[dict]:
    out = []
    base = revisions_dir(root)
    if not base.is_dir():
        return out
    active = active_revision(root)
    for d in sorted(p for p in base.iterdir() if p.is_dir() and valid_id(p.name)):
        manifest = {}
        try:
            manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        out.append({"id": d.name, "active": d.name == active, "path": str(d),
                    "json": (d / SNAPSHOT).is_file(), "sqlite": (d / DATABASE).is_file(),
                    **{k: manifest.get(k) for k in ("generated_at", "installed_at", "sha256", "sqlite_sha256")}})
    return out


def paths_for(root: Path, revision: str | None = None) -> tuple[Path, Path, str | None]:
    """(json path, sqlite path, revision id) to read: an explicit revision,
    else the active one, else the legacy files (revision None)."""
    rid = revision or active_revision(root)
    if revision and not revision_dir(root, revision).is_dir():
        raise ValueError(f"Data revision {revision} is not installed here.")
    if rid:
        d = revision_dir(root, rid)
        return d / SNAPSHOT, d / DATABASE, rid
    legacy = root / "data/processed" / SNAPSHOT
    return legacy, legacy.with_name(DATABASE), None


def staging_dir(root: Path) -> Path:
    base = revisions_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=".staging-", dir=base))


def set_active(root: Path, rid: str) -> None:
    """Point `active` at an installed revision, atomically."""
    if not revision_dir(root, rid).is_dir():
        raise ValueError(f"Data revision {rid} is not installed here.")
    pointer = revisions_dir(root) / "active"
    fd, tmp = tempfile.mkstemp(prefix=".active-", dir=pointer.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(rid + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, pointer)


def publish(root: Path, stage: Path, rid: str, *, activate: bool = True) -> Path:
    """Move a complete, checked staging directory into place as revision
    `rid` and (by default) make it active. An existing revision with the same
    id is kept only when its manifest checksums agree; a different content
    under the same id is refused, since an id names exactly one content."""
    target = revision_dir(root, rid)
    if target.exists():
        old = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        new = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
        same = all(old.get(k) == new.get(k) for k in ("sha256", "sqlite_sha256") if new.get(k) or old.get(k))
        if not same:
            raise ValueError(f"Revision {rid} is already installed with different content; nothing was replaced.")
        _rmtree(stage)
    else:
        os.rename(stage, target)
    if activate:
        set_active(root, rid)
    return target


def _rmtree(path: Path) -> None:
    for p in sorted(path.rglob("*"), reverse=True):
        p.unlink() if p.is_file() or p.is_symlink() else p.rmdir()
    path.rmdir()
