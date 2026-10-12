"""Persistent API conversations with shared benchmark answer verification."""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path

from . import providers


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique stage names also avoid collisions between separate terminals.
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".pending-", delete=False) as temporary:
            pending = Path(temporary.name)
            json.dump(value, temporary, ensure_ascii=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(pending, path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


class Session:
    def __init__(self, root: Path, provider: str, model: str, *, cid: str | None = None, language: str = "auto"):
        self.root = root
        self.directory = root / "data/bench2agent/chats"
        self.doc = {"id": secrets.token_hex(6), "provider": provider, "model": model, "language": language,
                    "title": "New conversation", "ts": int(time.time()), "messages": [], "turns": []}
        if cid:
            self.doc = self.load(cid)
        self.expected_revision = None if not cid else self.path.stat().st_mtime_ns

    @property
    def path(self) -> Path:
        return self.directory / (self.doc["id"] + ".json")

    def store(self):
        """The data this conversation reads, always as an explicit revision:
        the one its first answer was given on, or for a new conversation the
        one active at this first question. Pinned for the whole turn, so a
        publish in the middle of the question changes nothing it reads."""
        from bench2agent.core import revisions
        from bench2agent.core.benchmarks import BenchmarkStore
        sid = self.doc.get("snapshot_id")
        if sid:
            if not (revisions.valid_id(sid) and (revisions.revision_dir(self.root, sid) / "manifest.json").is_file()):
                raise ValueError(f"The dataset changed since this conversation: revision {sid} is no longer installed. "
                                 "Start /new, or reinstall that revision.")
            return BenchmarkStore(self.root, revision=sid)
        return BenchmarkStore(self.root, revision=revisions.active_revision(self.root))

    def load(self, cid: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{12}", cid):
            raise ValueError("Invalid conversation ID. Use bench2agent chats.")
        with (self.directory / (cid + ".json")).open(encoding="utf-8") as source:
            return json.load(source)

    def ask(self, question: str, emit) -> dict:
        from bench2agent.core import chat, mcp
        from bench2agent.core.policy import BENCHMARK_SYSTEM
        from bench2agent.core.verify import Verifier
        store = self.store()
        snapshot = store.data()
        if not snapshot["available"]:
            raise ValueError(snapshot.get("reason") or
                             "Benchmark data is missing. Run bench2agent data install --file FILE or --url URL.")
        sid = snapshot.get("snapshot_id")
        if self.doc.get("snapshot_id") and self.doc["snapshot_id"] != sid:
            raise ValueError("The dataset changed since this conversation. Start /new, or reinstall its snapshot.")
        if store.revision and store.revision != sid:
            raise ValueError(f"Revision {store.revision} holds snapshot {sid}; its directory needs recovery.")
        original_revision = self.path.stat().st_mtime_ns if self.path.exists() else None
        if original_revision != self.expected_revision:
            raise ValueError("This conversation changed in another terminal. Resume it again before continuing.")
        language = self.doc.get("language", "auto")
        system = BENCHMARK_SYSTEM + ("\nAnswer in the user's language." if language == "auto" else
                                    "\nWrite prose in " + {"ko": "Korean", "en": "English"}.get(language, language) + ".")
        with mcp.using(store):   # tools and the answer check read this revision, whatever is active
            answer = providers.generate(self.doc["provider"], self.doc["model"], system,
                                        self.doc["messages"], question, emit)
            if store.data().get("snapshot_id") != sid:
                raise ValueError("The dataset changed during this answer. Start a new turn; this answer was not saved.")
            corpus = mcp.Store()
            segs, verified = chat.segment(answer["text"], corpus, Verifier(corpus), answer["trail"])
        turn = {"q": question, "segs": segs, "verified": verified, "trail": answer["trail"],
                "usage": answer["usage"], "on": {"benchmarks": {key: snapshot.get(key)
                         for key in ("snapshot_id", "generated_at", "source")}}}
        if (self.path.stat().st_mtime_ns if self.path.exists() else None) != original_revision:
            raise ValueError("This conversation changed in another terminal; answer was not saved.")
        updated = {**self.doc, "messages": answer["messages"], "snapshot_id": sid, "ts": int(time.time()),
                   "title": self.doc["title"] if self.doc["turns"] else question[:80],
                   "turns": [*self.doc["turns"], turn]}
        write_json(self.path, updated)
        self.doc = updated
        self.expected_revision = self.path.stat().st_mtime_ns
        return turn


def conversations(root: Path) -> list[dict]:
    result = []
    for path in (root / "data/bench2agent/chats").glob("*.json"):
        try:
            with path.open(encoding="utf-8") as source:
                doc = json.load(source)
            result.append({key: doc.get(key) for key in ("id", "title", "provider", "model", "ts")})
        except (OSError, ValueError, AttributeError):
            continue
    return sorted(result, key=lambda doc: doc.get("ts") or 0, reverse=True)
