"""One isolated Claude Code runtime per web visitor.

The hosted web offers one way to answer: the official, unmodified Claude Code
binary, signed in by the visitor with their own Claude account through
Anthropic's own flow. Each visitor gets a runtime of their own:

    <root>/data/bench2agent/visitors/<visitor id>/     mode 0700
        home/                                          HOME and working directory
        home/.claude/                                  CLAUDE_CONFIG_DIR: the CLI's own login store

The process environment is built from an allowlist (binaries' PATH, TLS and
proxy settings) plus that HOME; the operator's login, keys and environment
never enter it. Bench2Agent never opens, copies or forwards what the CLI
stores there; "disconnect" asks the CLI to log out, then removes the
directory.

Sign-in is the CLI's own `claude auth login`, run in a pseudo-terminal. What
the CLI prints is relayed to the visitor's browser only, and what the visitor
types is relayed back; this module records the *shape* of the exchange
(started, url shown, awaiting input, success, failure) and never its content.

Isolation here is by directory and environment. It separates logins,
sessions and settings between visitors on one host; it is not an OS-user or
container boundary. The host has no privilege to create users, so that
stronger boundary is a deployment step, recorded in the design document.

Limits: a cap on runtimes, a cap on concurrent processes, and idle expiry
with cleanup, because a runtime costs disk and a process costs memory
whether or not the visitor ever signs in.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from bench2agent.runtime.cli_agents import ClaudeCodeConnector, isolated_environment, which

_ID = re.compile(r"[0-9a-f]{8,32}")
_URL = re.compile(r"https?://\S+")


class VisitorError(ValueError):
    pass


class LoginSession:
    """`claude auth login` under a PTY: output is streamed to the visitor,
    input is written back. Only state transitions are kept server-side."""

    def __init__(self, cmd: list[str], env: dict, cwd: Path, on_state):
        self.state = "starting"
        self.states: list[dict] = []
        self._on_state = on_state
        self._lock = threading.Lock()
        self._chunks: list[str] = []          # relayed to the visitor, never logged
        self._closed = False
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # child
            os.chdir(cwd)
            os.execvpe(cmd[0], cmd, env)
        self._mark("started")
        threading.Thread(target=self._reader, daemon=True).start()

    def _mark(self, state: str) -> None:
        self.state = state
        event = {"state": state, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        self.states.append(event)
        self._on_state(event)

    def _reader(self) -> None:
        buf = ""
        while True:
            try:
                r, _, _ = select.select([self.fd], [], [], 0.5)
            except (OSError, ValueError):
                break
            if r:
                try:
                    chunk = os.read(self.fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                text = chunk.decode("utf-8", "replace")
                with self._lock:
                    self._chunks.append(text)
                buf = (buf + text)[-4000:]
                low = buf.lower()
                if "http" in low and self.state == "started":
                    self._mark("url_shown")
                if "paste code" in low and self.state in ("started", "url_shown"):
                    self._mark("awaiting_code")
            else:
                pid, status = os.waitpid(self.pid, os.WNOHANG)
                if pid:
                    self._mark("exited_ok" if os.waitstatus_to_exitcode(status) == 0 else "exited_failed")
                    break
        if not self.state.startswith("exited"):
            # the PTY reported EOF (the child closed it); collect the exit status
            try:
                _, status = os.waitpid(self.pid, 0)
                self._mark("exited_ok" if os.waitstatus_to_exitcode(status) == 0 else "exited_failed")
            except ChildProcessError:
                self._mark("exited_failed")
        self._closed = True
        try:
            os.close(self.fd)
        except OSError:
            pass

    def read(self, since: int) -> tuple[str, int]:
        with self._lock:
            return "".join(self._chunks[since:]), len(self._chunks)

    def write(self, text: str) -> None:
        if self._closed:
            raise VisitorError("The sign-in process has ended.")
        if len(text) > 4000:
            raise VisitorError("Input too long.")
        os.write(self.fd, text.encode())

    def stop(self) -> None:
        if not self._closed:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    @property
    def finished(self) -> bool:
        return self.state.startswith("exited")


class VisitorRuntimes:
    def __init__(self, root: Path, *, max_runtimes: int = 50, max_processes: int = 4, idle_days: int = 30,
                 binary: str = "claude", log_path: Path | None = None):
        self.base = root / "data/bench2agent/visitors"
        self.base.mkdir(parents=True, exist_ok=True)
        os.chmod(self.base, 0o700)
        self.max_runtimes, self.max_processes, self.idle_days = max_runtimes, max_processes, idle_days
        self.binary = binary
        self.log_path = log_path or (root / "data/bench2agent/visitor-events.jsonl")
        self._logins: dict[str, LoginSession] = {}
        self._active: dict[str, str] = {}      # job id -> visitor, one entry per running question
        self._disconnecting: dict[str, str] = {}   # visitor -> state while a disconnect is in progress
        self._pending_cleanup: set[str] = set()    # runtimes whose jobs did not end in time
        self._lock = threading.Lock()
        # extra host variables allowed into visitor processes; empty in
        # production, set by tests to drive a stub CLI
        self.passthrough: tuple[str, ...] = ()

    # -- paths and environment -------------------------------------------
    def _check(self, visitor: str) -> None:
        if not _ID.fullmatch(visitor or ""):
            raise VisitorError("Invalid visitor id.")

    def dir(self, visitor: str) -> Path:
        self._check(visitor)
        return self.base / visitor

    def home(self, visitor: str) -> Path:
        return self.dir(visitor) / "home"

    def config_dir(self, visitor: str) -> Path:
        return self.home(visitor) / ".claude"

    def environment(self, visitor: str) -> dict:
        env = isolated_environment(self.home(visitor))
        env["CLAUDE_CONFIG_DIR"] = str(self.config_dir(visitor))
        env.update({k: os.environ[k] for k in self.passthrough if k in os.environ})
        return env

    def exists(self, visitor: str) -> bool:
        return self.dir(visitor).is_dir()

    def ensure(self, visitor: str) -> Path:
        d = self.dir(visitor)
        if not d.exists():
            if sum(1 for p in self.base.iterdir() if p.is_dir()) >= self.max_runtimes:
                raise VisitorError("This host has reached its limit of visitor runtimes; try again later.")
            d.mkdir(mode=0o700)
            (d / "home").mkdir(mode=0o700)
            (d / "home" / ".claude").mkdir(mode=0o700)
        self.touch(visitor)
        return d

    def touch(self, visitor: str) -> None:
        try:
            os.utime(self.dir(visitor))
        except FileNotFoundError:
            pass

    def _event(self, visitor: str, kind: str, **fields) -> None:
        """Shape only: which visitor, which step, which state. No content."""
        record = {"visitor": visitor, "kind": kind, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **fields}
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError:
            pass

    def connector(self, visitor: str) -> ClaudeCodeConnector:
        return ClaudeCodeConnector(config_dir=str(self.config_dir(visitor)), env=self.environment(visitor), cwd=str(self.home(visitor)))

    # -- status ---------------------------------------------------------------
    def status(self, visitor: str) -> dict:
        self._check(visitor)
        if not which(self.binary):
            return {"available": False, "connected": False, "detail": "Claude Code is not installed on this host."}
        if not self.exists(visitor):
            return {"available": True, "connected": False, "runtime": False, "detail": "not connected"}
        st = self.connector(visitor).status()
        login = self._logins.get(visitor)
        return {"available": True, "connected": st["connected"], "runtime": True, "account": st.get("account"),
                "detail": st.get("detail"), "login_state": login.state if login else None,
                "busy": visitor in self._active.values(), "disconnecting": visitor in self._disconnecting,
                "pending_cleanup": visitor in self._pending_cleanup}

    # -- sign-in --------------------------------------------------------------
    def _refuse_if_disconnecting(self, visitor: str) -> None:
        if visitor in self._disconnecting or visitor in self._pending_cleanup:
            raise VisitorError("This connection is being closed; wait for it to finish, then connect again.")

    def _processes(self) -> int:
        return len([s for s in self._logins.values() if not s.finished]) + len(self._active)

    def login_start(self, visitor: str) -> dict:
        self._refuse_if_disconnecting(visitor)
        self.ensure(visitor)
        with self._lock:
            running = self._logins.get(visitor)
            if running and not running.finished:
                return {"state": running.state, "already": True}
            if self._processes() >= self.max_processes:
                raise VisitorError("This host is at its process limit; try again in a moment.")
            path = which(self.binary)
            if not path:
                raise VisitorError("Claude Code is not installed on this host.")
            session = LoginSession([path, "auth", "login"], self.environment(visitor), self.home(visitor),
                                   on_state=lambda ev: self._event(visitor, "login", state=ev["state"]))
            self._logins[visitor] = session
        return {"state": session.state, "already": False}

    def login_read(self, visitor: str, since: int = 0) -> dict:
        session = self._logins.get(visitor)
        if session is None:
            raise VisitorError("No sign-in in progress.")
        text, cursor = session.read(since)
        return {"state": session.state, "output": text, "cursor": cursor, "finished": session.finished}

    def login_input(self, visitor: str, text: str) -> dict:
        session = self._logins.get(visitor)
        if session is None:
            raise VisitorError("No sign-in in progress.")
        session.write(text)
        self._event(visitor, "login_input", chars=len(text))
        self.touch(visitor)
        return {"state": session.state}

    def login_cancel(self, visitor: str) -> dict:
        session = self._logins.pop(visitor, None)
        if session:
            session.stop()
            self._event(visitor, "login", state="cancelled")
        return {"cancelled": bool(session)}

    # -- running questions: one slot per job, held until that job ends ---------
    def acquire(self, visitor: str, job_id: str) -> None:
        self._refuse_if_disconnecting(visitor)
        with self._lock:
            if self._processes() >= self.max_processes:
                raise VisitorError("This host is at its process limit; try again in a moment.")
            self._active[job_id] = visitor
        self.touch(visitor)

    def release(self, job_id: str) -> None:
        with self._lock:
            self._active.pop(job_id, None)

    def running_jobs(self, visitor: str) -> list[str]:
        with self._lock:
            return [j for j, v in self._active.items() if v == visitor]

    # -- disconnect: refuse new work, stop, wait for termination, official logout, remove
    def disconnect(self, visitor: str, *, stop_running=None, wait_seconds: float = 30.0) -> dict:
        """Closing a connection in order: no new logins or questions for this
        visitor; cancel what runs; wait until every job of the visitor has
        actually ended; then the official logout and the removal of the
        credential store. If the jobs do not end in time the runtime stays
        on disk as `pending_cleanup` (no deletion under a live process) and
        `finish_pending()` / `sweep()` complete it later."""
        self._check(visitor)
        steps = {"stopped": False, "logged_out": None, "removed": False, "waited_for": 0, "pending_cleanup": False}
        with self._lock:
            self._disconnecting[visitor] = "disconnecting"
        try:
            if stop_running is not None:
                steps["stopped"] = bool(stop_running())
            self.login_cancel(visitor)
            deadline = time.time() + wait_seconds
            while self.running_jobs(visitor) and time.time() < deadline:
                time.sleep(0.1)
            steps["waited_for"] = len(self.running_jobs(visitor))
            if self.running_jobs(visitor):
                with self._lock:
                    self._pending_cleanup.add(visitor)
                steps["pending_cleanup"] = True
                self._event(visitor, "disconnect", **steps)
                return steps
            steps.update(self._remove(visitor))
        finally:
            with self._lock:
                self._disconnecting.pop(visitor, None)
        self._event(visitor, "disconnect", **steps)
        return steps

    def finish_pending(self) -> list[str]:
        """Complete disconnects whose jobs have since ended."""
        done = []
        for visitor in list(self._pending_cleanup):
            if not self.running_jobs(visitor):
                self._remove(visitor)
                with self._lock:
                    self._pending_cleanup.discard(visitor)
                self._event(visitor, "disconnect_completed")
                done.append(visitor)
        return done

    def _remove(self, visitor: str) -> dict:
        steps = {"logged_out": None, "removed": False}
        if self.exists(visitor):
            path = which(self.binary)
            if path:
                try:
                    out = subprocess.run([path, "auth", "logout"], env=self.environment(visitor), cwd=self.home(visitor),
                                         capture_output=True, text=True, timeout=30)
                    steps["logged_out"] = out.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    steps["logged_out"] = False
            shutil.rmtree(self.dir(visitor), ignore_errors=True)
            steps["removed"] = not self.dir(visitor).exists()
        return steps

    # -- housekeeping ----------------------------------------------------------
    def sweep(self, now: float | None = None) -> list[str]:
        """Remove runtimes idle longer than idle_days (official logout first)."""
        now = time.time() if now is None else now
        removed = self.finish_pending()
        for d in list(self.base.iterdir()):
            if not d.is_dir() or not _ID.fullmatch(d.name):
                continue
            if self.running_jobs(d.name) or (d.name in self._logins and not self._logins[d.name].finished):
                continue
            if now - d.stat().st_mtime > self.idle_days * 86400:
                self.disconnect(d.name)
                removed.append(d.name)
        return removed

    def count(self) -> int:
        return sum(1 for p in self.base.iterdir() if p.is_dir())
