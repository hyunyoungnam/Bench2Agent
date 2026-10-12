"""Portable snapshot installation and release bundles; no corpus in the wheel."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import urllib.parse
from pathlib import Path

SNAPSHOT = "benchmark_snapshot.json"
MEMBER = "data/processed/" + SNAPSHOT
# A release-pinned default keeps first-run downloads reproducible and checked.
DEFAULT_URL = "https://github.com/hyunyoungnam/Bench2Agent/releases/download/data-20261007/bench2agent-data.tar.gz"
DEFAULT_SHA256 = "d19dcc2c47cc13738188176b5b87534e7e3eb0f0d11c86731bf41e0a1acca50e"


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def validate(path: Path) -> dict:
    from bench2agent.core.repository import validate_snapshot
    with path.open(encoding="utf-8") as source:
        return validate_snapshot(json.load(source))


def status(root: Path) -> dict:
    """What is installed and what the store would answer from. Top-level
    fields describe the resolved backend (BENCH2AGENT_STORE); `json` and
    `sqlite` describe each file on its own."""
    from bench2agent.core.benchmarks import BenchmarkStore
    from bench2agent.core.repository import DATABASE, SqliteRepository, counts, file_sha256
    json_path = root / MEMBER
    db = json_path.with_name(DATABASE)
    out = {"requested_backend": os.environ.get("BENCH2AGENT_STORE", "json")}
    out["json"] = ({"available": True, "path": str(json_path), **validate(json_path)} if json_path.exists()
                   else {"available": False, "path": str(json_path)})
    if db.exists():
        try:
            with SqliteRepository(db).session() as repo:
                ident = repo.identity()
            out["sqlite"] = {"available": True, "path": str(db), "sha256": file_sha256(db), **ident, **counts(db),
                             # the two files describe the same extraction only when the DB was built from this JSON
                             "matches_json": (ident["source_sha256"] == file_sha256(json_path)) if json_path.exists() else None}
        except ValueError as exc:
            out["sqlite"] = {"available": False, "path": str(db), "error": str(exc)}
    else:
        out["sqlite"] = {"available": False, "path": str(db)}
    try:
        resolved = BenchmarkStore(root).data()
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
    """Import the installed JSON snapshot into the SQLite file beside it."""
    from bench2agent.core.repository import DATABASE, import_snapshot
    source = root / MEMBER
    if not source.exists():
        raise ValueError("No benchmark snapshot installed; run bench2agent data install first.")
    return import_snapshot(source, source.with_name(DATABASE))


def _is_sqlite(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(16) == b"SQLite format 3\x00"


def _check_sqlite(path: Path) -> dict:
    from bench2agent.core.repository import DB_SCHEMA_VERSION, SqliteRepository
    from bench2agent.core.repository import SOURCE as DB_SOURCE
    with SqliteRepository(path).session() as repo:
        ident = repo.identity()
    if ident["db_schema_version"] != DB_SCHEMA_VERSION or ident["source"] != DB_SOURCE:
        raise ValueError("Unsupported benchmark database schema or extraction source.")
    return ident


def install(root: Path, *, file: str | None = None, url: str | None = None, sha256: str | None = None) -> dict:
    if not file and not url:
        url = DEFAULT_URL
        sha256 = sha256 or DEFAULT_SHA256
    elif file and url:
        raise ValueError("Choose one --file or --url.")
    target = root / MEMBER
    target.parent.mkdir(parents=True, exist_ok=True)
    # Stage on the same filesystem so os.replace is atomic. A failed download,
    # invalid bundle or bad checksum leaves the previous snapshot untouched.
    with tempfile.TemporaryDirectory(prefix=".bench2agent-", dir=target.parent) as directory:
        stage = Path(directory)
        if file:
            source = Path(file).expanduser().resolve()
        else:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
                raise ValueError("Data URL must use HTTPS (HTTP is allowed for localhost).")
            from bench2agent.core.cli import _download
            source = stage / "download"
            _download(url, source)
        if sha256 and digest(source).lower() != sha256.lower():
            raise ValueError("Data file SHA-256 does not match; existing data was kept.")
        pending = stage / SNAPSHOT
        if not tarfile.is_tarfile(source) and _is_sqlite(source):
            # a database alone is a complete install for BENCH2AGENT_STORE=sqlite|auto
            from bench2agent.core.repository import DATABASE, file_sha256
            staged = stage / DATABASE
            shutil.copyfile(source, staged)
            ident = _check_sqlite(staged)
            db_target = target.with_name(DATABASE)
            os.replace(staged, db_target)
            checksum = file_sha256(db_target)
            db_target.with_name(DATABASE + ".sha256").write_text(f"{checksum}  {DATABASE}\n", encoding="utf-8")
            return {"available": True, "path": str(db_target), "backend": "sqlite", "sha256": checksum, **ident}
        pending_db = None
        if tarfile.is_tarfile(source):
            with tarfile.open(source) as bundle:
                matches = [member for member in bundle.getmembers() if member.name == MEMBER]
                if len(matches) != 1 or not matches[0].isfile():
                    raise ValueError("Bundle must contain exactly one regular " + MEMBER)
                with bundle.extractfile(matches[0]) as incoming, pending.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                manifests = [member for member in bundle.getmembers() if member.name == "manifest.json"]
                if len(manifests) != 1 or not manifests[0].isfile():
                    raise ValueError("Bundle manifest is missing or invalid.")
                with bundle.extractfile(manifests[0]) as incoming:
                    manifest = json.load(incoming)
                if manifest.get("sha256") != digest(pending):
                    raise ValueError("Snapshot checksum in bundle manifest does not match.")
                from bench2agent.core.repository import DATABASE
                dbs = [member for member in bundle.getmembers() if member.name == "data/processed/" + DATABASE]
                if dbs:
                    pending_db = stage / DATABASE
                    with bundle.extractfile(dbs[0]) as incoming, pending_db.open("wb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
                    if manifest.get("sqlite_sha256") != digest(pending_db):
                        raise ValueError("Database checksum in bundle manifest does not match.")
                    _check_sqlite(pending_db)
        else:
            shutil.copyfile(source, pending)
        metadata = validate(pending)
        os.replace(pending, target)
        if pending_db is not None:
            db_target = target.with_name(pending_db.name)
            os.replace(pending_db, db_target)
            db_target.with_name(db_target.name + ".sha256").write_text(f"{digest(db_target)}  {db_target.name}\n", encoding="utf-8")
            metadata["sqlite"] = str(db_target)
    return {"available": True, "path": str(target), **metadata}


def bundle(root: Path, output: Path) -> dict:
    source = root / MEMBER
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
        from bench2agent.core.repository import DATABASE
        db = source.with_name(DATABASE)
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
                archive.add(frozen_db, arcname="data/processed/" + DATABASE)
            archive.add(manifest, arcname="manifest.json")
        os.replace(pending, output)
    checksum = digest(output)
    output.with_name(output.name + ".sha256").write_text(checksum + "  " + output.name + "\n", encoding="utf-8")
    return {"path": str(output), "sha256": checksum, **metadata, "includes_sqlite": frozen_db is not None}
