"""The web host's own store: users, tokens, owned conversations, usage.

Separate from the research data on purpose. Research revisions are immutable
and shared; this file (`data/bench2agent/web.sqlite`) is mutable and private
to one host. Standard library only. A token is kept as a SHA-256 hash and
shown to the operator once, at creation; the credential a user sends for a
model provider is never written here or anywhere.
"""
from __future__ import annotations

import contextlib
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
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, doc TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_conversations_user ON conversations(user_id, updated_at);
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, cid TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT, outcome TEXT NOT NULL, duration_ms INTEGER, tool_calls INTEGER,
    input_tokens INTEGER, output_tokens INTEGER, question TEXT,
    model_ms INTEGER, tool_ms INTEGER, verify_ms INTEGER);
CREATE INDEX IF NOT EXISTS ix_attempts_user ON attempts(user_id, started_at);
CREATE TABLE IF NOT EXISTS invites (
    code_hash TEXT PRIMARY KEY, label TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT,
    uses_left INTEGER NOT NULL, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS sessions (
    session_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id), created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL, revoked_at TEXT);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
"""
MIGRATIONS = [
    # earlier files lack the version column that conditional saves rely on
    ("conversations", "version", "ALTER TABLE conversations ADD COLUMN version INTEGER NOT NULL DEFAULT 0"),
    ("attempts", "model_ms", "ALTER TABLE attempts ADD COLUMN model_ms INTEGER"),
    ("attempts", "tool_ms", "ALTER TABLE attempts ADD COLUMN tool_ms INTEGER"),
    ("attempts", "verify_ms", "ALTER TABLE attempts ADD COLUMN verify_ms INTEGER"),
]


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
            for table, column, statement in MIGRATIONS:
                if column not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
                    con.execute(statement)

    @contextlib.contextmanager
    def _con(self):
        """A connection that is closed when the block ends, however it ends.
        (sqlite3's own context manager handles transactions, not closing.)
        Autocommit mode: multi-statement writes open their own BEGIN."""
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            yield con
        finally:
            con.close()

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
            con.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (_now(), row["user_id"]))
        return {"name": name, "disabled": True}

    def users(self) -> list[dict]:
        with self._con() as con:
            return [dict(r) for r in con.execute(
                "SELECT u.user_id, u.name, u.created_at, u.disabled, "
                "(SELECT COUNT(*) FROM tokens t WHERE t.user_id = u.user_id AND t.revoked_at IS NULL) AS tokens, "
                "(SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.user_id) AS conversations FROM users u ORDER BY u.name")]

    # -- invites and browser sessions: the web user's way in -----------------
    def add_invite(self, label: str, *, uses: int = 1, days: int | None = 14) -> dict:
        """An invite code the operator hands out; shown once. A user who
        redeems it gets an account and a browser session, never a token to
        copy."""
        code = "inv_" + secrets.token_urlsafe(16)
        expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)).isoformat(timespec="seconds") if days else None
        with self._lock, self._con() as con:
            con.execute("INSERT INTO invites VALUES (?,?,?,?,?,NULL)", (hash_token(code), label[:80], _now(), expires, max(1, uses)))
        return {"label": label, "code": code, "uses": max(1, uses), "expires_at": expires}

    def invites(self) -> list[dict]:
        with self._con() as con:
            return [dict(r) for r in con.execute(
                "SELECT label, created_at, expires_at, uses_left, revoked_at FROM invites ORDER BY created_at DESC")]

    def revoke_invite(self, label: str) -> int:
        with self._lock, self._con() as con:
            return con.execute("UPDATE invites SET revoked_at = ? WHERE label = ? AND revoked_at IS NULL", (_now(), label)).rowcount

    def redeem_invite(self, code: str, name: str) -> dict:
        """Create (or reuse by name, when that user was created by the same
        invite flow) a user and open a session. One transaction, so an
        invite with one use left is consumed exactly once."""
        name = (name or "").strip()
        if not name or len(name) > 80:
            raise ValueError("A display name is 1 to 80 characters.")
        if not code or len(code) > 200:
            raise ValueError("Invalid invite code.")
        session = "sess_" + secrets.token_urlsafe(32)
        now = _now()
        with self._lock, self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                inv = con.execute("SELECT code_hash, expires_at, uses_left, revoked_at FROM invites WHERE code_hash = ?",
                                  (hash_token(code),)).fetchone()
                if not inv or inv["revoked_at"] or inv["uses_left"] < 1 or (inv["expires_at"] and inv["expires_at"] < now):
                    raise ValueError("This invite is not valid (unknown, used up, expired or revoked).")
                row = con.execute("SELECT user_id, disabled FROM users WHERE name = ?", (name,)).fetchone()
                if row and row["disabled"]:
                    raise ValueError("This name belongs to a disabled account.")
                if row:
                    raise ValueError("This name is taken; choose another.")
                uid = secrets.token_hex(8)
                con.execute("INSERT INTO users VALUES (?,?,?,0)", (uid, name, now))
                con.execute("UPDATE invites SET uses_left = uses_left - 1 WHERE code_hash = ?", (inv["code_hash"],))
                expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat(timespec="seconds")
                con.execute("INSERT INTO sessions VALUES (?,?,?,?,NULL)", (hash_token(session), uid, now, expires))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return {"user_id": uid, "name": name, "session": session}

    def open_session(self, user_id: str) -> str:
        session = "sess_" + secrets.token_urlsafe(32)
        expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat(timespec="seconds")
        with self._lock, self._con() as con:
            con.execute("INSERT INTO sessions VALUES (?,?,?,?,NULL)", (hash_token(session), user_id, _now(), expires))
        return session

    def authenticate_session(self, session: str | None) -> dict | None:
        if not session or len(session) > 200:
            return None
        with self._con() as con:
            row = con.execute(
                "SELECT u.user_id, u.name FROM sessions s JOIN users u USING (user_id) WHERE s.session_hash = ? "
                "AND s.revoked_at IS NULL AND s.expires_at > ? AND u.disabled = 0", (hash_token(session), _now())).fetchone()
        return dict(row) if row else None

    def close_session(self, session: str | None) -> None:
        if not session:
            return
        with self._lock, self._con() as con:
            con.execute("UPDATE sessions SET revoked_at = ? WHERE session_hash = ?", (_now(), hash_token(session)))

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
            con.execute("INSERT INTO conversations (cid, user_id, revision, provider, model, language, title, created_at, "
                        "updated_at, doc) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (cid, user_id, revision, provider, model, language, "New conversation", now, now, json.dumps(doc)))
        return self.conversation(user_id, cid)

    def conversation(self, user_id: str, cid: str, *, with_doc: bool = True) -> dict | None:
        """Only the owner's conversations exist, as far as the API is concerned."""
        with self._con() as con:
            row = con.execute("SELECT * FROM conversations WHERE cid = ? AND user_id = ?", (cid, user_id)).fetchone()
        if not row:
            return None
        out = {k: row[k] for k in ("cid", "revision", "provider", "model", "language", "title", "created_at", "updated_at", "version")}
        if with_doc:
            doc = json.loads(row["doc"])
            out["turns"] = doc["turns"]
            out["messages"] = doc["messages"]
        return out

    def conversations(self, user_id: str) -> list[dict]:
        with self._con() as con:
            return [{k: r[k] for k in ("cid", "revision", "provider", "model", "title", "created_at", "updated_at")}
                    for r in con.execute("SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC", (user_id,))]

    def append_turn(self, user_id: str, cid: str, *, messages: list, turn: dict, expected_version: int) -> dict:
        """Save a finished, verified turn as a conditional update on the
        conversation's version number: the row must still be at the version
        the question started from, or the save is refused, so two overlapping
        turns can never interleave their histories (clock resolution plays no
        part)."""
        with self._lock, self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT doc, title, version FROM conversations WHERE cid = ? AND user_id = ?", (cid, user_id)).fetchone()
                if not row:
                    raise ValueError("Conversation not found.")
                if row["version"] != expected_version:
                    raise ValueError("The conversation changed while this answer was being produced; it was not saved.")
                doc = json.loads(row["doc"])
                doc["messages"] = messages
                doc["turns"].append(turn)
                title = row["title"] if doc["turns"][:-1] else (turn["q"][:80] or row["title"])
                changed = con.execute(
                    "UPDATE conversations SET doc = ?, title = ?, updated_at = ?, version = version + 1 WHERE cid = ? AND version = ?",
                    (json.dumps(doc, ensure_ascii=False), title, _now(), cid, expected_version)).rowcount
                if changed != 1:
                    raise ValueError("The conversation changed while this answer was being produced; it was not saved.")
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return self.conversation(user_id, cid)

    def delete_conversation(self, user_id: str, cid: str) -> bool:
        with self._lock, self._con() as con:
            return con.execute("DELETE FROM conversations WHERE cid = ? AND user_id = ?", (cid, user_id)).rowcount == 1

    # -- attempts: the unit the limit counts, reserved before the model is called
    @staticmethod
    def _since() -> str:
        return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat(timespec="seconds")

    def reserve_attempt(self, user_id: str, cid: str, question: str, limit: int) -> int | None:
        """Count the day's attempts and record this one in a single write
        transaction, so two requests arriving with one attempt left cannot
        both pass. Returns the attempt id, or None when the limit is reached.
        Cancelled and failed attempts count: the model was called."""
        with self._lock, self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                used = con.execute("SELECT COUNT(*) FROM attempts WHERE user_id = ? AND started_at >= ?",
                                   (user_id, self._since())).fetchone()[0]
                if used >= limit:
                    con.execute("ROLLBACK")
                    return None
                cur = con.execute("INSERT INTO attempts (user_id, cid, started_at, outcome, question) VALUES (?,?,?,?,?)",
                                  (user_id, cid, _now(), "running", question[:200]))
                con.execute("COMMIT")
                return cur.lastrowid
            except BaseException:
                con.execute("ROLLBACK")
                raise

    def finish_attempt(self, attempt_id: int, outcome: str, *, duration_ms: int, tool_calls: int = 0, usage: dict | None = None,
                       timing: dict | None = None) -> None:
        """outcome: saved, failed, cancelled, refused. No credential, no answer text."""
        usage = usage or {}
        timing = timing or {}
        with self._lock, self._con() as con:
            con.execute("UPDATE attempts SET finished_at = ?, outcome = ?, duration_ms = ?, tool_calls = ?, input_tokens = ?, "
                        "output_tokens = ?, model_ms = ?, tool_ms = ?, verify_ms = ? WHERE attempt_id = ?",
                        (_now(), outcome, duration_ms, tool_calls,
                         usage.get("input_tokens") or usage.get("prompt_tokens"),
                         usage.get("output_tokens") or usage.get("completion_tokens"),
                         timing.get("model_ms"), timing.get("tool_ms"), timing.get("verify_ms"), attempt_id))

    def usage_today(self, user_id: str) -> dict:
        with self._con() as con:
            rows = con.execute("SELECT outcome, COUNT(*) FROM attempts WHERE user_id = ? AND started_at >= ? GROUP BY outcome",
                               (user_id, self._since())).fetchall()
        by = {r[0]: r[1] for r in rows}
        return {"attempts": sum(by.values()), "answers": by.get("saved", 0), "by_outcome": by}

    def attempts(self, user_name: str | None = None, limit: int = 50) -> list[dict]:
        """Recent attempts with outcome and timing, for review beside the
        saved conversation (cid + attempt id link them)."""
        with self._con() as con:
            sql = ("SELECT a.attempt_id, u.name, a.cid, a.started_at, a.finished_at, a.outcome, a.duration_ms, a.model_ms, "
                   "a.tool_ms, a.verify_ms, a.tool_calls, a.input_tokens, a.output_tokens, a.question "
                   "FROM attempts a JOIN users u USING (user_id)")
            args: tuple = ()
            if user_name:
                sql += " WHERE u.name = ?"
                args = (user_name,)
            sql += " ORDER BY a.attempt_id DESC LIMIT ?"
            return [dict(r) for r in con.execute(sql, (*args, limit))]
