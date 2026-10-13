"""Immutable data revisions and the one pointer that says which is active.

    <root>/data/revisions/<revision_id>/benchmark_snapshot.sqlite  (+ .sha256)
    <root>/data/revisions/<revision_id>/manifest.json
    <root>/data/revisions/active        # the revision id being served

A revision directory is written completely into a staging directory, checked,
moved into place with one rename, and only then named in `active` with one
replace. Nothing that fails before that last step changes what is served,
and a revision once published is never edited: a saved conversation keeps
reading the revision it started on while new conversations get the new one.

There is no fallback to other files. No `active` pointer means nothing is
installed yet; a pointer whose target is missing or damaged is an error that
names the recovery, never a silent switch to older data.

Two manifest formats are read:

- `bench2agent-data-v2` (from 2026-10-13): the database is the revision.
  `files` lists every file with its sha256; `content_hashes` records the
  content under every known rule (`repository.HASH_RULES`), computed from
  the database itself at install; `revision_id` (= `snapshot_id`) is derived
  from the `v2-logical` hash; `db_schema_version` and `min_client_version`
  say whether this code can read it. A revision installed from a legacy JSON
  source keeps that file beside the database (`legacy_json`), listed and
  checked like any other, never queried.
- `bench2agent-data-v1` (installed by 0.4.0 and earlier): JSON + database,
  `sha256` / `sqlite_sha256`, a stamp-derived `snapshot_id`, one
  `content_sha256` under the `v1-json` rule. Still served; never rewritten.

Hashes compare only under the same rule; an unknown rule is an error. A hash
a bundle or database offers is verified against the database content at
install, never copied; the installed manifest records what was computed.

Repair is a restore: a damaged file may only be replaced by bytes that match
the manifest's recorded checksum; the manifest is never rewritten to fit.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .repository import (CONTENT_HASH_RULE, DATABASE, HASH_RULES, SNAPSHOT, SUPPORTED_SCHEMAS, SqliteRepository,
                         client_supports, content_hashes, content_sha256, export_snapshot,  # noqa: F401
                         file_sha256)

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
POINTER = "active"
FORMAT_V1 = "bench2agent-data-v1"
FORMAT_V2 = "bench2agent-data-v2"


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


def files_of(manifest: dict) -> dict[str, str]:
    """name -> recorded sha256 of every file the manifest vouches for."""
    if manifest.get("format") == FORMAT_V2 or isinstance(manifest.get("files"), dict):
        return {k: v for k, v in (manifest.get("files") or {}).items() if v}
    out = {}
    if manifest.get("sha256"):
        out[SNAPSHOT] = manifest["sha256"]
    if manifest.get("sqlite_sha256"):
        out[DATABASE] = manifest["sqlite_sha256"]
    return out


def json_sha256(manifest: dict) -> str | None:
    """The checksum of the legacy JSON the revision came from, when one is recorded."""
    return files_of(manifest).get(SNAPSHOT) or manifest.get("source_json_sha256")


def hashes_of(manifest: dict) -> dict[str, str]:
    """rule -> content hash the manifest records. A v1 manifest recorded one
    hash under the v1-json rule; a v2 manifest records `content_hashes`.
    An unknown rule is refused."""
    if manifest.get("format") == FORMAT_V2 or "content_hashes" in manifest:
        out = {r: h for r, h in (manifest.get("content_hashes") or {}).items() if h}
        if manifest.get("content_sha256"):
            out.setdefault(manifest.get("content_hash_rule") or CONTENT_HASH_RULE, manifest["content_sha256"])
    else:
        out = {"v1-json": manifest["content_sha256"]} if manifest.get("content_sha256") else {}
    unknown = sorted(r for r in out if r not in HASH_RULES)
    if unknown:
        raise RevisionError(f"Unknown content hash rule {unknown[0]!r} in a manifest; upgrade bench2agent to read this data.")
    return out


def compare_hashes(a: dict, b: dict) -> bool | None:
    """Whether two hash sets describe the same content: compared under every
    rule both record (all must agree); None when they share no rule."""
    common = set(a) & set(b)
    if not common:
        return None
    return all(a[r] == b[r] for r in common)


def database_hashes(db_path: Path) -> dict[str, str]:
    """The content of a database under every known rule, read from the file."""
    return content_hashes(export_snapshot(db_path))


def same_content(old: dict, new: dict) -> bool | None:
    """Do two manifests describe the same research content? Decided by hashes
    under a common rule; None when they share none."""
    return compare_hashes(hashes_of(old), hashes_of(new))


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
    revision, its schema is one this code reads and its minimum client
    version is met. Returns the manifest."""
    manifest = manifest if manifest is not None else json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    files = files_of(manifest)
    if not files:
        problems.append("manifest records no files")
    for name, expected in files.items():
        p = path / name
        if not p.is_file():
            problems.append(f"{name} is missing (expected sha256 {expected})")
        elif file_sha256(p) != expected:
            problems.append(f"{name} does not match its recorded sha256 {expected}")
    db_path = path / DATABASE
    if not problems and db_path.is_file():
        try:
            with SqliteRepository(db_path).session() as repo:
                ident = repo.identity()
        except ValueError as exc:
            problems.append(str(exc))
        else:
            rid = manifest.get("revision_id") or manifest.get("snapshot_id")
            if (ident.get("revision_id") or ident.get("snapshot_id")) != rid:
                problems.append("database identifies a different revision")
            if ident.get("db_schema_version") not in SUPPORTED_SCHEMAS:
                problems.append(f"database schema {ident.get('db_schema_version')} is not readable by this Bench2Agent")
            needs = manifest.get("min_client_version") or ident.get("min_client_version")
            if needs and not client_supports(needs):
                problems.append(f"revision needs bench2agent {needs} or newer")
            try:
                recorded = hashes_of(manifest)
            except RevisionError as exc:
                recorded, _ = {}, problems.append(str(exc))
            if ident.get("content_sha256"):
                rule = ident.get("content_hash_rule") or CONTENT_HASH_RULE
                if rule not in HASH_RULES:
                    problems.append(f"database records a content hash under unknown rule {rule!r}")
                elif recorded.get(rule) and recorded[rule] != ident["content_sha256"]:
                    problems.append("database records a different content hash than the manifest")
            json_sha = files_of(manifest).get(SNAPSHOT)
            if json_sha and ident.get("source_sha256") and ident["source_sha256"] != json_sha:
                problems.append("database was built from a different JSON than the one installed")
    elif not problems:
        problems.append(f"{DATABASE} is missing")
    if problems:
        raise RevisionError(f"Revision {manifest.get('revision_id') or manifest.get('snapshot_id', path.name)} failed verification: "
                            + "; ".join(problems)
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
                    "format": manifest.get("format"),
                    "json": (d / SNAPSHOT).is_file(), "sqlite": (d / DATABASE).is_file(),
                    **{k: manifest.get(k) for k in ("generated_at", "installed_at", "content_sha256", "content_hash_rule",
                                                    "min_client_version")},
                    "sqlite_sha256": files_of(manifest).get(DATABASE)})
    return out


def paths_for(root: Path, revision: str | None = None) -> tuple[Path | None, str | None]:
    """(database path, revision id) to read. An explicit revision must be
    installed; otherwise the active one. (None, None) when nothing is
    installed. A broken pointer or a missing explicit revision raises
    RevisionError."""
    rid = revision or active_revision(root)
    if rid is None:
        return None, None
    d = revision_dir(root, rid)
    if not (d / "manifest.json").is_file():
        raise RevisionError(f"Data revision {rid} is not installed here. " + recovery_hint(root, rid))
    return d / DATABASE, rid


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
        same = same_content(old, new)
        if same is None:
            # the installed manifest records no comparable hash (a bare database
            # installed by 0.4.0): equal bytes prove equal content; otherwise
            # the installed database is read, if it is intact
            old_db, new_db = files_of(old).get(DATABASE), files_of(new).get(DATABASE)
            if old_db and old_db == new_db:
                same = True
            elif old_db and (target / DATABASE).is_file() and file_sha256(target / DATABASE) == old_db:
                same = compare_hashes(database_hashes(target / DATABASE), hashes_of(new))
            if same is None:
                raise RevisionError(f"Revision {rid} is installed without a comparable content hash and its database "
                                    "cannot be read; nothing was replaced.")
        if not same:
            raise RevisionError(f"Revision {rid} is already installed with different content; nothing was replaced.")
        try:
            verify_dir(target, old)
            outcome = "reused"
        except RevisionError:
            restored = restore_damaged(target, old, stage)
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


def damaged_files(target: Path, manifest: dict) -> list[str]:
    """The files of an installed revision that do not match its manifest."""
    return [name for name, expected in files_of(manifest).items()
            if not (target / name).is_file() or file_sha256(target / name) != expected]


def restore_damaged(target: Path, manifest: dict, offered: Path) -> list[str]:
    """Put back only the damaged files of `target`, and only from files in
    `offered` whose bytes match the manifest's recorded checksums. Each file
    is copied to a temporary file beside its destination, checked there, and
    swapped in with one replace, so a failure mid-copy leaves every existing
    file as it was and no temporary file behind. Healthy files are not
    touched; the manifest is never rewritten."""
    restored = []
    expected_of = files_of(manifest)
    for name in damaged_files(target, manifest):
        source = offered / name
        if not source.is_file() or file_sha256(source) != expected_of[name]:
            continue
        fd, tmp = tempfile.mkstemp(prefix=".restore-", dir=target)
        os.close(fd)
        tmp = Path(tmp)
        try:
            shutil.copyfile(source, tmp)
            if file_sha256(tmp) != expected_of[name]:
                raise RevisionError(f"Copy of {name} did not reproduce its checksum; nothing was replaced.")
            os.replace(tmp, target / name)
        finally:
            tmp.unlink(missing_ok=True)
        restored.append(name)
    return restored


def _rmtree(path: Path) -> None:
    for p in sorted(path.rglob("*"), reverse=True):
        p.unlink() if p.is_file() or p.is_symlink() else p.rmdir()
    path.rmdir()
