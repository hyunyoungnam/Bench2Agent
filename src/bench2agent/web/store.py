"""The web host's own store: users, tokens, owned conversations, usage.

Separate from the research data on purpose. Research revisions are immutable
and shared; this file (`data/bench2agent/web.sqlite`) is mutable and private
to one host. Standard library only. A token is kept as a SHA-256 hash and
shown to the operator once, at creation; the credential a user sends for a
model provider is never written here or anywhere.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id), created_at TEXT NOT NULL,
    revoked_at TEXT);
CREATE TABLE IF NOT EXISTS conversations (
    cid TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id), revision TEXT NOT NULL,
    provider TEXT NOT NULL, model TEXT NOT NULL, language TEXT NOT NULL, title TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, doc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_conversations_user ON conversations(user_id, updated_at);
CREATE TABLE IF NOT EXISTS turn_log (
    user_id TEXT NOT NULL, cid TEXT NOT NULL, at TEXT NOT NULL, outcome TEXT NOT NULL,
    input_tokens INTEGER, output_tokens INTEGER);
CREATE INDEX IF NOT EXISTS ix_turn_log_user ON turn_log(user_id, at);
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class WebStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._con() as con:
            con.executescript(SCHEMA)

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    # -- users and tokens ----------------------------------------------------
    def add_user(self, name: str) -> dict:
        """Create a user and one token. The token is returned exactly once."""
        if not name or len(name) > 80:
            raise ValueError("A user name is 1 to 80 characters.")
        token = "b2a_" + secrets.token_urlsafe(32)
        with self._lock, self._con() as con:
            if con.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone():
                raise ValueError(f"User {name!r} already exists; use token add to issue another token.")
            uid = secrets.token_hex(8)
            con.execute("INSERT INTO users VALUES (?,?,?,0)", (uid, name, _now()))
            con.execute("INSERT INTO tokens VALUES (?,?,?,NULL)", (hash_token(token), uid, _now()))
        return {"user_id": uid, "name": name, "token": token}

    def add_token(self, name: str) -> dict:
        token = "b2a_" + secrets.token_urlsafe(32)
        with self._lock, self._con() as con:
            row = con.execute("SELECT user_id FROM users WHERE name = ? AND disabled = 0", (name,)).fetchone()
            if not row:
                raise ValueError(f"No active user {name!r}.")
            con.execute("INSERT INTO tokens VALUES (?,?,?,NULL)", (hash_token(token), row["user_id"], _now()))
        return {"name": name, "token": token}

    def revoke_user(self, name: str) -> dict:
        with self._lock, self._con() as con:
            row = con.execute("SELECT user_id FROM users WHERE name = ?", (name,)).fetchone()
            if not row:
                raise ValueError(f"No user {name!r}.")
            con.execute("UPDATE users SET disabled = 1 WHERE user_id = ?", (row["user_id"],))
            con.execute("UPDATE tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (_now(), row["user_id"]))
        return {"name": name, "disabled": True}

    def users(self) -> list[dict]:
        with self._con() as con:
            return [dict(r) for r in con.execute(
                "SELECT u.user_id, u.name, u.created_at, u.disabled, "
                "(SELECT COUNT(*) FROM tokens t WHERE t.user_id = u.user_id AND t.revoked_at IS NULL) AS tokens, "
                "(SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.user_id) AS conversations FROM users u ORDER BY u.name")]

    def authenticate(self, token: str | None) -> dict | None:
        if not token or len(token) > 200:
            return None
        with self._con() as con:
            row = con.execute(
                "SELECT u.user_id, u.name FROM tokens t JOIN users u USING (user_id) "
                "WHERE t.token_hash = ? AND t.revoked_at IS NULL AND u.disabled = 0", (hash_token(token),)).fetchone()
        return dict(row) if row else None

    # -- conversations -------------------------------------------------------
    def create_conversation(self, user_id: str, *, revision: str, provider: str, model: str, language: str) -> dict:
        cid = secrets.token_hex(6)
        now = _now()
        doc = {"messages": [], "turns": []}
        with self._lock, self._con() as con:
            con.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (cid, user_id, revision, provider, model, language, "New conversation", now, now, json.dumps(doc)))
        return self.conversation(user_id, cid)

    def conversation(self, user_id: str, cid: str, *, with_doc: bool = True) -> dict | None:
        """Only the owner's conversations exist, as far as the API is concerned."""
        with self._con() as con:
            row = con.execute("SELECT * FROM conversations WHERE cid = ? AND user_id = ?", (cid, user_id)).fetchone()
        if not row:
            return None
        out = {k: row[k] for k in ("cid", "revision", "provider", "model", "language", "title", "created_at", "updated_at")}
        if with_doc:
            doc = json.loads(row["doc"])
            out["turns"] = doc["turns"]
            out["messages"] = doc["messages"]
        return out

    def conversations(self, user_id: str) -> list[dict]:
        with self._con() as con:
            return [{k: r[k] for k in ("cid", "revision", "provider", "model", "title", "created_at", "updated_at")}
                    for r in con.execute("SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC", (user_id,))]

    def append_turn(self, user_id: str, cid: str, *, messages: list, turn: dict, expected_updated_at: str) -> dict:
        """Save a finished, verified turn. Refused when the conversation moved
        on meanwhile (another request of the same user), so two overlapping
        turns cannot interleave their histories."""
        with self._lock, self._con() as con:
            row = con.execute("SELECT doc, title, updated_at FROM conversations WHERE cid = ? AND user_id = ?", (cid, user_id)).fetchone()
            if not row:
                raise ValueError("Conversation not found.")
            if row["updated_at"] != expected_updated_at:
                raise ValueError("The conversation changed while this answer was being produced; it was not saved.")
            doc = json.loads(row["doc"])
            doc["messages"] = messages
            doc["turns"].append(turn)
            title = row["title"] if doc["turns"][:-1] else (turn["q"][:80] or row["title"])
            now = _now()
            con.execute("UPDATE conversations SET doc = ?, title = ?, updated_at = ? WHERE cid = ?",
                        (json.dumps(doc, ensure_ascii=False), title, now, cid))
        return self.conversation(user_id, cid)

    def delete_conversation(self, user_id: str, cid: str) -> bool:
        with self._lock, self._con() as con:
            return con.execute("DELETE FROM conversations WHERE cid = ? AND user_id = ?", (cid, user_id)).rowcount == 1

    # -- usage ---------------------------------------------------------------
    def log_turn(self, user_id: str, cid: str, outcome: str, usage: dict | None = None) -> None:
        usage = usage or {}
        with self._lock, self._con() as con:
            con.execute("INSERT INTO turn_log VALUES (?,?,?,?,?,?)",
                        (user_id, cid, _now(), outcome,
                         usage.get("input_tokens") or usage.get("prompt_tokens"),
                         usage.get("output_tokens") or usage.get("completion_tokens")))

    def turns_today(self, user_id: str) -> int:
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat(timespec="seconds")
        with self._con() as con:
            return con.execute("SELECT COUNT(*) FROM turn_log WHERE user_id = ? AND at >= ? AND outcome IN ('saved', 'failed')",
                               (user_id, since)).fetchone()[0]
