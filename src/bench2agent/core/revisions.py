"""Immutable data revisions and the one pointer that says which is active.

    <root>/data/revisions/<snapshot_id>/benchmark_snapshot.json
    <root>/data/revisions/<snapshot_id>/benchmark_snapshot.sqlite  (+ .sha256)
    <root>/data/revisions/<snapshot_id>/manifest.json
    <root>/data/revisions/active        # the snapshot_id being served

A revision directory is written completely into a staging directory, checked,
moved into place with one rename, and only then named in `active` with one
replace. Nothing that fails before that last step changes what is served,
and a revision once published is never edited: a saved conversation keeps
reading the revision it started on while new conversations get the new one.

There is no fallback to other files. No `active` pointer means nothing is
installed yet; a pointer whose target is missing or damaged is an error that
names the recovery, never a silent switch to older data.

Identity, kept apart on purpose:
- `snapshot_id` names the extraction *inputs* (it is derived from their file
  stamps, not from the content) and is the revision id;
- `content_sha256` is the content with `generated_at` removed, so a re-export
  of the same extraction is recognised as the same revision;
- `sha256` / `sqlite_sha256` are the bytes actually installed. A damaged copy
  may only be restored from bytes that match them; the manifest is never
  rewritten to fit a new file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .repository import DATABASE, SNAPSHOT, SqliteRepository, file_sha256

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
POINTER = "active"


class RevisionError(ValueError):
    """A pointer or revision that needs recovery, not a missing install."""


def revisions_dir(root: Path) -> Path:
    return root / "data/revisions"


def valid_id(rid: str) -> bool:
    return bool(rid) and bool(_ID.fullmatch(rid)) and rid not in (POINTER, ".", "..")


def revision_dir(root: Path, rid: str) -> Path:
    if not valid_id(rid):
        raise ValueError(f"Invalid revision id: {rid!r}")
    return revisions_dir(root) / rid


def content_sha256(data: dict) -> str:
    """The snapshot's content, independent of when it was exported."""
    body = {k: v for k, v in data.items() if k != "generated_at"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def pointer_state(root: Path) -> tuple[str, str | None]:
    """("none", None) with no pointer; ("ok", rid) when its target directory
    holds a manifest; ("broken", rid) otherwise."""
    try:
        rid = (revisions_dir(root) / POINTER).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "none", None
    if not valid_id(rid) or not (revisions_dir(root) / rid / "manifest.json").is_file():
        return "broken", rid or None
    return "ok", rid


def recovery_hint(root: Path, rid: str | None) -> str:
    installed = [d.name for d in sorted(revisions_dir(root).iterdir())
                 if d.is_dir() and valid_id(d.name) and (d / "manifest.json").is_file()] if revisions_dir(root).is_dir() else []
    choice = (f"run `bench2agent data activate <id>` with one of: {', '.join(installed)}" if installed
              else "reinstall a verified bundle with `bench2agent data install`")
    return f"Active data revision {rid!r} is not installed or is damaged; {choice}."


def active_revision(root: Path) -> str | None:
    """The served revision id, None when nothing is installed; a broken
    pointer raises RevisionError with the recovery."""
    state, rid = pointer_state(root)
    if state == "broken":
        raise RevisionError(recovery_hint(root, rid))
    return rid


def manifest_of(root: Path, rid: str) -> dict:
    return json.loads((revision_dir(root, rid) / "manifest.json").read_text(encoding="utf-8"))


def verify_dir(path: Path, manifest: dict | None = None) -> dict:
    """Check a revision directory against its manifest: every listed file is
    present with the recorded checksum, the database identifies the same
    snapshot and was built from the same JSON. Returns the manifest."""
    manifest = manifest if manifest is not None else json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    json_path, db_path = path / SNAPSHOT, path / DATABASE
    for name, p, key in ((SNAPSHOT, json_path, "sha256"), (DATABASE, db_path, "sqlite_sha256")):
        expected = manifest.get(key)
        if not expected:
            continue
        if not p.is_file():
            problems.append(f"{name} is missing (expected sha256 {expected})")
        elif file_sha256(p) != expected:
            problems.append(f"{name} does not match its recorded sha256 {expected}")
    if not manifest.get("sha256") and not manifest.get("sqlite_sha256"):
        problems.append("manifest records no files")
    if not problems and db_path.is_file():
        try:
            with SqliteRepository(db_path).session() as repo:
                ident = repo.identity()
        except ValueError as exc:
            problems.append(str(exc))
        else:
            if ident.get("snapshot_id") != manifest.get("snapshot_id"):
                problems.append("database identifies a different snapshot")
            if manifest.get("sha256") and ident.get("source_sha256") != manifest["sha256"]:
                problems.append("database was built from a different JSON than the one installed")
    if problems:
        raise RevisionError(f"Revision {manifest.get('snapshot_id', path.name)} failed verification: " + "; ".join(problems)
                            + ". Restore the original files from a verified bundle or backup; the manifest is not rewritten.")
    return manifest


def verify_revision(root: Path, rid: str) -> dict:
    d = revision_dir(root, rid)
    if not (d / "manifest.json").is_file():
        raise RevisionError(f"Data revision {rid} is not installed here. " + recovery_hint(root, rid))
    return verify_dir(d)


def list_revisions(root: Path) -> list[dict]:
    out = []
    base = revisions_dir(root)
    if not base.is_dir():
        return out
    state, active = pointer_state(root)
    for d in sorted(p for p in base.iterdir() if p.is_dir() and valid_id(p.name)):
        manifest = {}
        try:
            manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        out.append({"id": d.name, "active": d.name == active and state == "ok", "path": str(d),
                    "json": (d / SNAPSHOT).is_file(), "sqlite": (d / DATABASE).is_file(),
                    **{k: manifest.get(k) for k in ("generated_at", "installed_at", "sha256", "sqlite_sha256", "content_sha256")}})
    return out


def paths_for(root: Path, revision: str | None = None) -> tuple[Path | None, Path | None, str | None]:
    """(json path, sqlite path, revision id) to read. An explicit revision
    must be installed; otherwise the active one. (None, None, None) when
    nothing is installed. A broken pointer or a missing explicit revision
    raises RevisionError."""
    rid = revision or active_revision(root)
    if rid is None:
        return None, None, None
    d = revision_dir(root, rid)
    if not (d / "manifest.json").is_file():
        raise RevisionError(f"Data revision {rid} is not installed here. " + recovery_hint(root, rid))
    return d / SNAPSHOT, d / DATABASE, rid


def staging_dir(root: Path) -> Path:
    base = revisions_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=".staging-", dir=base))


def set_active(root: Path, rid: str) -> dict:
    """Point `active` at an installed revision, atomically, after verifying it."""
    manifest = verify_revision(root, rid)
    pointer = revisions_dir(root) / POINTER
    fd, tmp = tempfile.mkstemp(prefix=".active-", dir=pointer.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(rid + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, pointer)
    return manifest


def publish(root: Path, stage: Path, rid: str, *, activate: bool = True) -> tuple[Path, str]:
    """Move a complete, checked staging directory into place as revision
    `rid` and (by default) make it active. Returns (directory, outcome):
    "installed", "reused" (same id and same content already installed and
    healthy; the existing copy and manifest are kept), or "restored" (same
    content, the installed copy was damaged, and the staged bytes match the
    original manifest's checksums, so those files were put back). Same id
    with different content is refused; a damaged copy that cannot be matched
    byte for byte is refused with the checksums it needs."""
    verify_dir(stage)
    target = revision_dir(root, rid)
    outcome = "installed"
    if target.exists():
        new = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
        try:
            old = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise RevisionError(f"Revision {rid} exists without a readable manifest; remove or restore that "
                                f"directory from a verified bundle before installing.") from None
        if old.get("content_sha256") != new.get("content_sha256"):
            raise RevisionError(f"Revision {rid} is already installed with different content; nothing was replaced.")
        try:
            verify_dir(target, old)
            outcome = "reused"
        except RevisionError:
            restored = []
            for name, key in ((SNAPSHOT, "sha256"), (DATABASE, "sqlite_sha256")):
                staged = stage / name
                if old.get(key) and staged.is_file() and file_sha256(staged) == old[key]:
                    shutil.copyfile(staged, target / name)
                    restored.append(name)
            try:
                verify_dir(target, old)
            except RevisionError as exc:
                raise RevisionError(f"{exc} The offered files do not match the original checksums"
                                    + (f" (restored: {', '.join(restored)})" if restored else "") + ".") from None
            outcome = "restored"
        _rmtree(stage)
    else:
        os.rename(stage, target)
    if activate:
        set_active(root, rid)
    return target, outcome


def _rmtree(path: Path) -> None:
    for p in sorted(path.rglob("*"), reverse=True):
        p.unlink() if p.is_file() or p.is_symlink() else p.rmdir()
    path.rmdir()
