"""Portable data installation as immutable revisions; no corpus in the wheel.

An install stages the revision's database (and, for a legacy JSON source,
that JSON beside it), checks everything, moves the directory into
`data/revisions/<revision_id>/` and then switches the `active` pointer
(`core.revisions`). A failure at any step keeps the previously active
revision serving. Earlier revisions stay installed for saved conversations
and `data activate` can switch back to one.

Sources: a data-v2 bundle (database + manifest), a data-v1 bundle (JSON +
database + manifest, as released up to data-20261013), a bare
`benchmark_snapshot.sqlite` written by `bench2agent benchmarks`, or a legacy
`benchmark_snapshot.json`, imported once into a database and kept beside it,
listed in the manifest and checked like any file, never queried.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import tarfile
import tempfile
import urllib.parse
from pathlib import Path

from bench2agent.core import revisions
from bench2agent.core.repository import (CONTENT_HASH_RULE, DATABASE, HASH_RULES, MIN_CLIENT_VERSION, SNAPSHOT, SOURCE,
                                         SUPPORTED_SCHEMAS, SqliteRepository, client_supports, content_hashes, counts,
                                         export_snapshot, file_sha256, import_snapshot, version_tuple)
from bench2agent.core.revisions import FORMAT_V2, RevisionError

MEMBER = "data/processed/" + SNAPSHOT
DB_MEMBER = "data/processed/" + DATABASE
# A release-pinned default keeps first-run downloads reproducible and checked.
# data-20261013 is a data-v1 bundle; it installs through the legacy import.
DEFAULT_URL = "https://github.com/hyunyoungnam/Bench2Agent/releases/download/data-20261013/bench2agent-data.tar.gz"
DEFAULT_SHA256 = "9195fce6afd7e4ae7448be67bd9a6f17e5374bbbbe602f3900737088f69d3af2"


def digest(path: Path) -> str:
    return file_sha256(path)


def validate(path: Path) -> dict:
    """A legacy JSON snapshot's shape and identity."""
    from bench2agent.core.repository import validate_snapshot
    with path.open(encoding="utf-8") as source:
        return validate_snapshot(json.load(source))


def _is_sqlite(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(16) == b"SQLite format 3\x00"


def _check_sqlite(path: Path) -> dict:
    """The database's identity, after the checks that decide whether this
    code can read it at all."""
    with SqliteRepository(path).session() as repo:
        ident = repo.identity()
    if ident["db_schema_version"] not in SUPPORTED_SCHEMAS or ident["source"] != SOURCE:
        raise ValueError("Unsupported benchmark database schema or extraction source.")
    if ident.get("min_client_version") and not client_supports(ident["min_client_version"]):
        raise ValueError(f"This data needs bench2agent {ident['min_client_version']} or newer; upgrade the code first.")
    return ident


def _describe(db_path: Path) -> dict:
    if not db_path.exists():
        return {"available": False, "path": str(db_path)}
    try:
        ident = _check_sqlite(db_path)
        return {"available": True, "path": str(db_path), "sha256": file_sha256(db_path), **ident, **counts(db_path)}
    except ValueError as exc:
        return {"available": False, "path": str(db_path), "error": str(exc)}


def status(root: Path, revision: str | None = None) -> dict:
    """What is installed and what the store would answer from. Top-level
    fields describe the served revision; `database` describes its file;
    `revisions` lists every installed revision and which is active."""
    from bench2agent.core.benchmarks import BenchmarkStore
    state, pointed = revisions.pointer_state(root)
    out = {"pointer": state, "revisions": revisions.list_revisions(root)}
    try:
        db_path, rid = revisions.paths_for(root, revision)
    except RevisionError as exc:
        return {**out, "available": False, "revision": revision or pointed, "reason": str(exc)}
    out["revision"] = rid
    if rid is None:
        return {**out, "available": False,
                "reason": "No data revision is installed. Run bench2agent data install (or data install --file FILE)."}
    try:
        manifest = revisions.manifest_of(root, rid)
    except (OSError, ValueError):
        manifest = {}
    out["format"] = manifest.get("format")
    out["files"] = sorted(revisions.files_of(manifest))
    out["database"] = _describe(db_path)
    try:
        revisions.verify_revision(root, rid)
        out["verification"] = "ok"
    except RevisionError as exc:
        out["verification"] = str(exc)
    try:
        resolved = BenchmarkStore(root, revision=rid).data()
    except ValueError as exc:
        return {**out, "available": False, "reason": str(exc)}
    detail = out["database"]
    out.update({"available": resolved["available"], "path": detail.get("path"),
                **{k: resolved.get(k) for k in ("snapshot_id", "generated_at", "source")},
                **{k: detail.get(k) for k in ("editions", "benchmarks", "paper_observations")}})
    if resolved.get("reason"):
        out["reason"] = resolved["reason"]
    return out


def build_sqlite(root: Path, source: str, out: str) -> dict:
    """Convert a legacy JSON snapshot (data format v1) into a database file.
    Published revisions are immutable, so the output may not land inside
    data/revisions/; recovery of a damaged revision is a reinstall of the
    verified original, never a rebuild in place."""
    src = Path(source).expanduser().resolve()
    target = Path(out).expanduser().resolve()
    base = revisions.revisions_dir(root).resolve()
    if target == base or base in target.parents:
        raise ValueError("Published revisions are immutable; write the database elsewhere and reinstall a verified "
                         "bundle to repair one.")
    if not src.is_file():
        raise ValueError(f"Snapshot JSON not found: {src}")
    return import_snapshot(src, target)


def activate(root: Path, rid: str) -> dict:
    """Serve an already installed, verified revision (rollback or roll-forward)."""
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
    """Put the database and/or the legacy JSON into `stage`; return the
    bundle's manifest (empty for a bare file). Every file a bundle carries
    must match the checksum its manifest records for it."""
    if not tarfile.is_tarfile(source):
        shutil.copyfile(source, stage / (DATABASE if _is_sqlite(source) else SNAPSHOT))
        return {}
    with tarfile.open(source) as bundle:
        members = {m.name: m for m in bundle.getmembers() if m.isfile()}
        if "manifest.json" not in members:
            raise ValueError("Bundle manifest is missing or invalid.")
        if MEMBER not in members and DB_MEMBER not in members:
            raise ValueError(f"Bundle must contain a regular {DB_MEMBER} (or, for data-v1 bundles, {MEMBER}).")
        with bundle.extractfile(members["manifest.json"]) as incoming:
            manifest = json.load(incoming)
        if not isinstance(manifest, dict):
            raise ValueError("Bundle manifest is missing or invalid.")
        recorded = revisions.files_of(manifest)
        for member, name in ((MEMBER, SNAPSHOT), (DB_MEMBER, DATABASE)):
            if member not in members:
                continue
            with bundle.extractfile(members[member]) as incoming, (stage / name).open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if recorded.get(name) != digest(stage / name):
                raise ValueError(f"{name} checksum in bundle manifest does not match.")
        return manifest


def install(root: Path, *, file: str | None = None, url: str | None = None, sha256: str | None = None,
            activate: bool = True) -> dict:
    """Install a bundle, a database or a legacy snapshot JSON as a new revision.

    Everything is staged and checked first: a database is checked for a
    readable schema and a supported client version; a legacy JSON is
    validated and imported. Every content hash the source offers — in the
    bundle manifest, in the database's own meta, or computed from a legacy
    JSON — is verified against the staged database's content under its own
    rule; an unknown rule or a mismatch refuses the install. The installed
    manifest records the hashes computed from the database. Only a complete,
    consistent directory is moved into place, and only then does `active`
    move. A failed install leaves the active revision exactly as it was."""
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
            bundle_manifest = _unpack(source, stage)
        json_path, db_path = stage / SNAPSHOT, stage / DATABASE
        legacy = json_path.exists()
        built_locally = False
        offered: dict[str, set[str]] = {}       # rule -> every hash some source vouched for

        def offer(hashes: dict, who: str):
            for rule, value in hashes.items():
                if rule not in HASH_RULES:
                    raise ValueError(f"Unknown content hash rule {rule!r} ({who}); upgrade bench2agent to read this data.")
                offered.setdefault(rule, set()).add(value)
        offer(revisions.hashes_of(bundle_manifest) if bundle_manifest else {}, "bundle manifest")
        if legacy:
            with json_path.open(encoding="utf-8") as fh:
                loaded = json.load(fh)
            from bench2agent.core.repository import validate_snapshot
            metadata = validate_snapshot(loaded)
            offer(content_hashes(loaded), "legacy JSON")
            del loaded
            if not db_path.exists():
                import_snapshot(json_path, db_path)      # the one-time legacy conversion
                built_locally = True
            ident = _check_sqlite(db_path)
            if ident["source_sha256"] != digest(json_path) or ident["snapshot_id"] != metadata["snapshot_id"]:
                raise ValueError("The database in this source was built from a different snapshot; nothing was installed.")
            rid = metadata["snapshot_id"]
        else:
            ident = _check_sqlite(db_path)
            rid = ident.get("revision_id") or ident["snapshot_id"]
            metadata = {"snapshot_id": ident["snapshot_id"], "generated_at": ident.get("generated_at"),
                        "source": ident["source"], **counts(db_path)}
        if ident.get("content_sha256"):
            offer({ident.get("content_hash_rule") or CONTENT_HASH_RULE: ident["content_sha256"]}, "database meta")
        # the database's actual content, under every rule: what every offer must match
        computed = content_hashes(export_snapshot(db_path))
        for rule, values in offered.items():
            if values != {computed[rule]}:
                raise ValueError(f"The database's content does not reproduce the content hash offered under rule {rule}; "
                                 "nothing was installed.")
        if not revisions.valid_id(rid):
            raise ValueError(f"Revision identity {rid!r} cannot name a revision directory.")
        import sqlite3
        from bench2agent import __version__
        needs = max((MIN_CLIENT_VERSION, ident.get("min_client_version") or "0", bundle_manifest.get("min_client_version") or "0"),
                    key=version_tuple)
        if not client_supports(needs):
            raise ValueError(f"This data needs bench2agent {needs} or newer; upgrade the code first.")
        files = {DATABASE: digest(db_path), **({SNAPSHOT: digest(json_path)} if legacy else {})}
        manifest = {"format": FORMAT_V2, "revision_id": rid, "snapshot_id": rid,
                    **{k: metadata.get(k) for k in ("generated_at", "source", "editions", "benchmarks", "paper_observations")},
                    "db_schema_version": ident["db_schema_version"], "min_client_version": needs,
                    "content_sha256": computed[CONTENT_HASH_RULE], "content_hash_rule": CONTENT_HASH_RULE,
                    "content_hashes": computed, "content_hash_from": "database",
                    "files": files, "legacy_json": legacy,
                    "source_json_sha256": ident.get("source_sha256"),
                    # bytes of a locally built database differ between SQLite
                    # builds even for identical content; say where they came from
                    "sqlite_built_locally": built_locally,
                    "sqlite_library": sqlite3.sqlite_version if built_locally else bundle_manifest.get("sqlite_library"),
                    "built_with": f"bench2agent {__version__}" if built_locally else (
                        bundle_manifest.get("built_with") or bundle_manifest.get("importer")),
                    "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        db_path.with_name(DATABASE + ".sha256").write_text(f"{files[DATABASE]}  {DATABASE}\n", encoding="utf-8")
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        target, outcome = revisions.publish(root, stage, rid, activate=activate)
    except BaseException:
        if stage.exists():
            revisions._rmtree(stage)
        raise
    kept = revisions.manifest_of(root, rid)   # the installed manifest, which a reuse or restore keeps
    return {"available": True, "revision": rid, "path": str(target), "active": activate, "outcome": outcome,
            "format": kept.get("format"), "files": sorted(revisions.files_of(kept)),
            **{k: kept.get(k) for k in ("snapshot_id", "generated_at", "source", "content_sha256", "content_hash_rule",
                                        "content_hashes", "db_schema_version", "min_client_version", "sqlite_built_locally",
                                        "editions", "benchmarks", "paper_observations")},
            "sqlite_sha256": revisions.files_of(kept).get(DATABASE)}


def bundle(root: Path, output: Path, revision: str | None = None) -> dict:
    """Pack a verified revision (the served one by default) as a data-v2
    bundle: its database and a manifest describing it. A legacy JSON kept
    beside the database is not shipped; the database is the revision."""
    db, rid = revisions.paths_for(root, revision)
    if rid is None:
        raise ValueError("No data revision is installed to bundle.")
    installed = revisions.verify_revision(root, rid)
    output = output.expanduser().resolve()
    if output == db:
        raise ValueError("Bundle output cannot replace the source database.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".bench2agent-", dir=output.parent) as directory:
        stage = Path(directory)
        # A frozen copy binds metadata and checksum to the exact bytes shipped.
        frozen = stage / DATABASE
        shutil.copyfile(db, frozen)
        ident = _check_sqlite(frozen)
        # the content under every rule, from the bytes shipped; what the
        # installed manifest recorded must agree under every rule it has
        computed = content_hashes(export_snapshot(frozen))
        if revisions.compare_hashes(revisions.hashes_of(installed), computed) is False:
            raise ValueError(f"Revision {rid}'s recorded content hash does not match its database; nothing was bundled.")
        manifest_data = {"format": FORMAT_V2, "revision_id": rid, "snapshot_id": rid,
                         **{k: installed.get(k) for k in ("generated_at", "source", "editions", "benchmarks", "paper_observations",
                                                          "sqlite_built_locally", "sqlite_library", "built_with",
                                                          "source_json_sha256")},
                         "content_sha256": computed[CONTENT_HASH_RULE], "content_hash_rule": CONTENT_HASH_RULE,
                         "content_hashes": computed,
                         "db_schema_version": ident["db_schema_version"],
                         "min_client_version": installed.get("min_client_version") or MIN_CLIENT_VERSION,
                         "files": {DATABASE: digest(frozen)}}
        manifest = stage / "manifest.json"
        manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
        pending = stage / "bundle.tar.gz"
        with tarfile.open(pending, "w:gz") as archive:
            archive.add(frozen, arcname=DB_MEMBER)
            archive.add(manifest, arcname="manifest.json")
        os.replace(pending, output)
    checksum = digest(output)
    output.with_name(output.name + ".sha256").write_text(checksum + "  " + output.name + "\n", encoding="utf-8")
    return {"path": str(output), "sha256": checksum, "revision": rid, "format": FORMAT_V2,
            **{k: manifest_data.get(k) for k in ("snapshot_id", "generated_at", "source", "editions", "benchmarks",
                                                 "paper_observations", "content_sha256", "content_hash_rule", "content_hashes",
                                                 "min_client_version")}}
