"""Persistent API conversations with shared benchmark answer verification."""
from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no advisory locks; conditional saves still apply
    fcntl = None

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
    def __init__(self, root: Path, provider: str, model: str | None, *, cid: str | None = None, language: str = "auto",
                 connector: str = "api"):
        self.root = root
        self.directory = root / "data/bench2agent/chats"
        self.doc = {"id": secrets.token_hex(6), "provider": provider, "model": model, "language": language,
                    "connector": connector, "title": "New conversation", "ts": int(time.time()), "messages": [], "turns": []}
        if cid:
            self.doc = self.load(cid)
        self.expected_revision = None if not cid else self.path.stat().st_mtime_ns

    @property
    def path(self) -> Path:
        return self.directory / (self.doc["id"] + ".json")

    @contextlib.contextmanager
    def _locked(self):
        """One writer at a time per conversation file (advisory lock beside it)."""
        self.directory.mkdir(parents=True, exist_ok=True)
        lock = self.directory / (self.doc["id"] + ".lock")
        with lock.open("a") as fh:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(fh, fcntl.LOCK_UN)

    def _current_revision(self):
        return self.path.stat().st_mtime_ns if self.path.exists() else None

    def _save(self, doc: dict) -> None:
        """Write only if the file is still the version this session read; then
        this session's expected revision follows its own successful write and
        nothing else. Another terminal's newer turns are never overwritten."""
        with self._locked():
            if self._current_revision() != self.expected_revision:
                raise ValueError("This conversation changed in another terminal. Resume it again before continuing.")
            write_json(self.path, doc)
            self.doc = doc
            self.expected_revision = self._current_revision()

    def _drop_provider_session(self) -> None:
        """A cancelled, failed, unverifiable or unsaved turn must not be
        continued inside the CLI's own session; forget it, keep our turns.
        Saved conditionally: if another terminal moved the file on, we drop
        the id in memory only and leave their version alone."""
        if self.doc.get("provider_session") is None:
            return
        doc = {k: v for k, v in self.doc.items() if k != "provider_session"}
        if not self.path.exists():
            self.doc = doc
            return
        try:
            self._save(doc)
        except ValueError:
            self.doc = doc

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
        if self._current_revision() != self.expected_revision:
            raise ValueError("This conversation changed in another terminal. Resume it again before continuing.")
        language = self.doc.get("language", "auto")
        system = BENCHMARK_SYSTEM + ("\nAnswer in the user's language." if language == "auto" else
                                    "\nWrite prose in " + {"ko": "Korean", "en": "English"}.get(language, language) + ".")
        from bench2agent.runtime import ConnectorError, connector
        name = self.doc.get("connector", "api")
        conn = connector(name, provider=self.doc["provider"]) if name == "api" else connector(name, model=self.doc["model"])
        tool_log = self.directory / "toollogs" / f"{self.doc['id']}-{int(time.time() * 1000)}.jsonl"
        tool_log.parent.mkdir(parents=True, exist_ok=True)
        from bench2agent.core import figures
        with mcp.using(store):   # the answer check reads this revision, whatever is active
            try:
                # a CLI connector resumes its own session only after a saved,
                # verified turn; otherwise it starts fresh from the saved turns
                run = conn.ask(root=self.root, revision=store.revision, question=question, system=system,
                               history=self.doc["messages"] if name == "api" else self.doc["turns"],
                               provider_session=self.doc.get("provider_session") or (self.doc["model"] if name == "api" else None),
                               emit=emit, cancelled=lambda: False, tool_log=tool_log)
            except ConnectorError as exc:
                self._drop_provider_session()
                raise providers.ProviderError(str(exc)) from None
            if store.data().get("snapshot_id") != sid:
                raise ValueError("The dataset changed during this answer. Start a new turn; this answer was not saved.")
            trail = run.trail_for_turn()
            if run.unverifiable:
                segs, verified = [{"t": "p", "s": run.text}], None
            else:
                corpus = mcp.Store()
                segs, verified = chat.segment(run.text, corpus, Verifier(corpus), trail, figures.TurnLog(run.trail))
        turn = {"q": question, "segs": segs, "verified": verified, "unverifiable": run.unverifiable, "trail": trail,
                "connector": name, "tool_log": tool_log.name if tool_log.exists() else None,
                "usage": run.usage, "on": {"benchmarks": {key: snapshot.get(key)
                         for key in ("snapshot_id", "revision", "generated_at", "source")}}}
        updated = {**self.doc, "messages": run.messages if run.messages is not None else self.doc["messages"],
                   "snapshot_id": sid, "ts": int(time.time()),
                   "title": self.doc["title"] if self.doc["turns"] else question[:80],
                   "turns": [*self.doc["turns"], turn]}
        updated.pop("provider_session", None)
        if name != "api" and run.provider_session and not run.unverifiable:
            updated["provider_session"] = run.provider_session
        try:
            self._save(updated)
        except ValueError:
            self.doc.pop("provider_session", None)        # not resumed after an unsaved turn
            raise ValueError("This conversation changed in another terminal; answer was not saved.")
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
