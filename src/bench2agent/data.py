"""Portable data installation as immutable revisions; no corpus in the wheel.

An install stages the snapshot JSON and its SQLite import together, checks
both, moves the directory into `data/revisions/<snapshot_id>/` and then
switches the `active` pointer (`core.revisions`). A failure at any step keeps
the previously active revision serving, with its JSON and database still
from one version. Earlier revisions stay installed for saved conversations
and `data activate` can switch back to one.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import urllib.parse
from pathlib import Path

from bench2agent.core import revisions
from bench2agent.core.repository import DATABASE, DB_SCHEMA_VERSION, SOURCE, SqliteRepository, counts, file_sha256, import_snapshot

SNAPSHOT = "benchmark_snapshot.json"
MEMBER = "data/processed/" + SNAPSHOT
DB_MEMBER = "data/processed/" + DATABASE
# A release-pinned default keeps first-run downloads reproducible and checked.
DEFAULT_URL = "https://github.com/hyunyoungnam/Bench2Agent/releases/download/data-20261007/bench2agent-data.tar.gz"
DEFAULT_SHA256 = "d19dcc2c47cc13738188176b5b87534e7e3eb0f0d11c86731bf41e0a1acca50e"


def digest(path: Path) -> str:
    return file_sha256(path)


def validate(path: Path) -> dict:
    from bench2agent.core.repository import validate_snapshot
    with path.open(encoding="utf-8") as source:
        return validate_snapshot(json.load(source))


def _is_sqlite(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(16) == b"SQLite format 3\x00"


def _check_sqlite(path: Path) -> dict:
    with SqliteRepository(path).session() as repo:
        ident = repo.identity()
    if ident["db_schema_version"] != DB_SCHEMA_VERSION or ident["source"] != SOURCE:
        raise ValueError("Unsupported benchmark database schema or extraction source.")
    return ident


def _describe(json_path: Path, db_path: Path) -> dict:
    out = {"json": ({"available": True, "path": str(json_path), **validate(json_path)} if json_path.exists()
                    else {"available": False, "path": str(json_path)})}
    if db_path.exists():
        try:
            ident = _check_sqlite(db_path)
            out["sqlite"] = {"available": True, "path": str(db_path), "sha256": file_sha256(db_path), **ident, **counts(db_path),
                             # the two files describe the same extraction only when the DB was built from this JSON
                             "matches_json": (ident["source_sha256"] == file_sha256(json_path)) if json_path.exists() else None}
        except ValueError as exc:
            out["sqlite"] = {"available": False, "path": str(db_path), "error": str(exc)}
    else:
        out["sqlite"] = {"available": False, "path": str(db_path)}
    return out


def status(root: Path, revision: str | None = None) -> dict:
    """What is installed and what the store would answer from. Top-level
    fields describe the resolved backend (BENCH2AGENT_STORE) of the served
    revision; `json` and `sqlite` describe each of its files; `revisions`
    lists every installed revision and which is active."""
    from bench2agent.core.benchmarks import BenchmarkStore
    json_path, db_path, rid = revisions.paths_for(root, revision)
    out = {"requested_backend": os.environ.get("BENCH2AGENT_STORE", "json"), "revision": rid,
           "layout": "revisions" if rid else "legacy", **_describe(json_path, db_path),
           "revisions": revisions.list_revisions(root)}
    try:
        resolved = BenchmarkStore(root, revision=revision).data()
    except ValueError as exc:
        return {**out, "available": False, "reason": str(exc)}
    backend = resolved["backend"]
    detail = out.get(backend) or {}
    out.update({"available": resolved["available"], "backend": backend, "path": detail.get("path"),
                **{k: resolved.get(k) for k in ("snapshot_id", "generated_at", "source")},
                **{k: detail.get(k) for k in ("editions", "benchmarks", "paper_observations")}})
    if resolved.get("reason"):
        out["reason"] = resolved["reason"]
    return out


def build_sqlite(root: Path) -> dict:
    """Import the served JSON snapshot into the SQLite file beside it (legacy
    layout, or the active revision's directory)."""
    source, db, _ = revisions.paths_for(root)
    if not source.exists():
        raise ValueError("No benchmark snapshot installed; run bench2agent data install first.")
    return import_snapshot(source, db)


def activate(root: Path, rid: str) -> dict:
    """Serve an already installed revision (rollback or roll-forward)."""
    revisions.set_active(root, rid)
    return status(root)


def _fetch(url: str, into: Path) -> Path:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("Data URL must use HTTPS (HTTP is allowed for localhost).")
    from bench2agent.core.cli import _download
    target = into / "download"
    _download(url, target)
    return target


def _unpack(source: Path, stage: Path) -> dict:
    """Put the snapshot (and database, when the source carries one) into
    `stage`; return the manifest fields known so far."""
    if not tarfile.is_tarfile(source):
        if _is_sqlite(source):
            shutil.copyfile(source, stage / DATABASE)
            return {}
        shutil.copyfile(source, stage / SNAPSHOT)
        return {}
    with tarfile.open(source) as bundle:
        members = {m.name: m for m in bundle.getmembers() if m.isfile()}
        if MEMBER not in members:
            raise ValueError("Bundle must contain exactly one regular " + MEMBER)
        if "manifest.json" not in members:
            raise ValueError("Bundle manifest is missing or invalid.")
        with bundle.extractfile(members["manifest.json"]) as incoming:
            manifest = json.load(incoming)
        with bundle.extractfile(members[MEMBER]) as incoming, (stage / SNAPSHOT).open("wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        if manifest.get("sha256") != digest(stage / SNAPSHOT):
            raise ValueError("Snapshot checksum in bundle manifest does not match.")
        if DB_MEMBER in members:
            with bundle.extractfile(members[DB_MEMBER]) as incoming, (stage / DATABASE).open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if manifest.get("sqlite_sha256") != digest(stage / DATABASE):
                raise ValueError("Database checksum in bundle manifest does not match.")
        return manifest


def install(root: Path, *, file: str | None = None, url: str | None = None, sha256: str | None = None,
            activate: bool = True) -> dict:
    """Install a bundle, a snapshot JSON or a SQLite file as a new revision.

    Everything is staged and checked first: the JSON is validated, the
    database is imported from it when the source has none, and both are
    checked against each other. Only a complete, consistent directory is
    moved into place, and only then does `active` move. A failed install
    leaves the active revision exactly as it was, both files included."""
    if not file and not url:
        url = DEFAULT_URL
        sha256 = sha256 or DEFAULT_SHA256
    elif file and url:
        raise ValueError("Choose one --file or --url.")
    stage = revisions.staging_dir(root)
    try:
        with tempfile.TemporaryDirectory(prefix=".download-", dir=stage.parent) as downloads:
            source = Path(file).expanduser().resolve() if file else _fetch(url, Path(downloads))
            if sha256 and digest(source).lower() != sha256.lower():
                raise ValueError("Data file SHA-256 does not match; existing data was kept.")
            manifest = _unpack(source, stage)
        json_path, db_path = stage / SNAPSHOT, stage / DATABASE
        if json_path.exists():
            metadata = validate(json_path)
            if not db_path.exists():
                import_snapshot(json_path, db_path)      # every revision serves both backends
            ident = _check_sqlite(db_path)
            if ident["source_sha256"] != digest(json_path) or ident["snapshot_id"] != metadata["snapshot_id"]:
                raise ValueError("The database in this source was built from a different snapshot; nothing was installed.")
        else:
            ident = _check_sqlite(db_path)
            metadata = {"snapshot_id": ident["snapshot_id"], "source": ident["source"]}
        rid = metadata["snapshot_id"]
        if not revisions.valid_id(rid):
            raise ValueError(f"Snapshot identity {rid!r} cannot name a revision directory.")
        manifest = {**{k: v for k, v in manifest.items() if k in ("format", "generated_at")},
                    "format": manifest.get("format", "bench2agent-data-v1"), **metadata,
                    "sha256": digest(json_path) if json_path.exists() else None,
                    "sqlite_sha256": digest(db_path), "db_schema_version": ident["db_schema_version"],
                    "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        db_path.with_name(DATABASE + ".sha256").write_text(f"{manifest['sqlite_sha256']}  {DATABASE}\n", encoding="utf-8")
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        target = revisions.publish(root, stage, rid, activate=activate)
    except BaseException:
        if stage.exists():
            revisions._rmtree(stage)
        raise
    return {"available": True, "revision": rid, "path": str(target), "active": activate,
            **{k: manifest.get(k) for k in ("snapshot_id", "generated_at", "source", "sha256", "sqlite_sha256",
                                              "editions", "benchmarks", "paper_observations")}}


def bundle(root: Path, output: Path) -> dict:
    """Pack the served revision (JSON plus its SQLite file when it matches)."""
    source, db, _ = revisions.paths_for(root)
    metadata = validate(source)
    output = output.expanduser().resolve()
    if output == source:
        raise ValueError("Bundle output cannot replace the source snapshot.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".bench2agent-", dir=output.parent) as directory:
        stage = Path(directory)
        # A frozen copy binds metadata and checksum to the exact bytes shipped.
        frozen = stage / SNAPSHOT
        shutil.copyfile(source, frozen)
        metadata = validate(frozen)
        manifest_data = {"format": "bench2agent-data-v1", "sha256": digest(frozen), **metadata}
        frozen_db = None
        if db.exists():
            frozen_db = stage / DATABASE
            shutil.copyfile(db, frozen_db)
            ident = _check_sqlite(frozen_db)
            if ident["source_sha256"] != manifest_data["sha256"]:
                raise ValueError("The SQLite file was built from a different snapshot; run bench2agent data sqlite first.")
            manifest_data.update({"sqlite_sha256": digest(frozen_db), "db_schema_version": ident["db_schema_version"]})
        manifest = stage / "manifest.json"
        manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
        pending = stage / "bundle.tar.gz"
        with tarfile.open(pending, "w:gz") as archive:
            archive.add(frozen, arcname=MEMBER)
            if frozen_db is not None:
                archive.add(frozen_db, arcname=DB_MEMBER)
            archive.add(manifest, arcname="manifest.json")
        os.replace(pending, output)
    checksum = digest(output)
    output.with_name(output.name + ".sha256").write_text(checksum + "  " + output.name + "\n", encoding="utf-8")
    return {"path": str(output), "sha256": checksum, **metadata, "includes_sqlite": frozen_db is not None}
