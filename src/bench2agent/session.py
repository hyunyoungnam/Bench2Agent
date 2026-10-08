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

    def load(self, cid: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{12}", cid):
            raise ValueError("Invalid conversation ID. Use bench2agent chats.")
        with (self.directory / (cid + ".json")).open(encoding="utf-8") as source:
            return json.load(source)

    def ask(self, question: str, emit) -> dict:
        from bench2agent.core import chat, mcp
        from bench2agent.core.policy import BENCHMARK_SYSTEM
        from bench2agent.core.verify import Verifier
        snapshot = mcp.B.data()
        if not snapshot["available"]:
            raise ValueError("Benchmark data is missing. Run bench2agent data install --file FILE or --url URL.")
        sid = snapshot.get("snapshot_id")
        if self.doc.get("snapshot_id") and self.doc["snapshot_id"] != sid:
            raise ValueError("The dataset changed since this conversation. Start /new, or reinstall its snapshot.")
        original_revision = self.path.stat().st_mtime_ns if self.path.exists() else None
        if original_revision != self.expected_revision:
            raise ValueError("This conversation changed in another terminal. Resume it again before continuing.")
        language = self.doc.get("language", "auto")
        system = BENCHMARK_SYSTEM + ("\nAnswer in the user's language." if language == "auto" else
                                    "\nWrite prose in " + {"ko": "Korean", "en": "English"}.get(language, language) + ".")
        answer = providers.generate(self.doc["provider"], self.doc["model"], system,
                                    self.doc["messages"], question, emit)
        if mcp.B.data().get("snapshot_id") != sid:
            raise ValueError("The dataset changed during this answer. Start a new turn; this answer was not saved.")
        store = mcp.Store()
        segs, verified = chat.segment(answer["text"], store, Verifier(store), answer["trail"])
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
