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
    with path.open(encoding="utf-8") as source:
        data = json.load(source)
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("source") != "arxiv_html_stated_roles":
        raise ValueError("Unsupported benchmark snapshot schema or extraction source.")
    if not isinstance(data.get("snapshot_id"), str) or not data["snapshot_id"]:
        raise ValueError("Snapshot identity is missing.")
    for field, kind in (("editions", dict), ("benchmarks", list), ("papers", list)):
        if not isinstance(data.get(field), kind):
            raise ValueError(f"Invalid snapshot field: {field}")
    editions = data["editions"]
    ids = {entry["id"] for entry in data["benchmarks"] if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
    if len(ids) != len(data["benchmarks"]):
        raise ValueError("Invalid or duplicate benchmark identities.")
    for paper in data["papers"]:
        if not isinstance(paper, dict) or paper.get("edition") not in editions or not isinstance(paper.get("uses"), dict):
            raise ValueError("Invalid paper or edition in snapshot.")
    return {"snapshot_id": data["snapshot_id"], "generated_at": data.get("generated_at"),
            "source": data["source"], "editions": len(editions), "benchmarks": len(ids), "paper_observations": len(data["papers"])}


def status(root: Path) -> dict:
    path = root / MEMBER
    if not path.exists():
        return {"available": False, "path": str(path)}
    return {"available": True, "path": str(path), **validate(path)}


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
        else:
            shutil.copyfile(source, pending)
        metadata = validate(pending)
        os.replace(pending, target)
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
        manifest = stage / "manifest.json"
        manifest.write_text(json.dumps({"format": "bench2agent-data-v1", "sha256": digest(frozen), **metadata}), encoding="utf-8")
        pending = stage / "bundle.tar.gz"
        with tarfile.open(pending, "w:gz") as archive:
            archive.add(frozen, arcname=MEMBER)
            archive.add(manifest, arcname="manifest.json")
        os.replace(pending, output)
    checksum = digest(output)
    output.with_name(output.name + ".sha256").write_text(checksum + "  " + output.name + "\n", encoding="utf-8")
    return {"path": str(output), "sha256": checksum, **metadata}
