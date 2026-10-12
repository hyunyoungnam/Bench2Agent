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

    def stop(self, sig=signal.SIGTERM) -> None:
        """Send a termination signal; `finished` says whether it worked."""
        if not self.finished:
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:
                pass

    def wait(self, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end and not self.finished:
            time.sleep(0.05)
        return self.finished

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
        self._logins: dict[str, LoginSession | None] = {}   # None: reserved, process starting
        self._active: dict[str, str] = {}      # job id -> visitor, one entry per running question
        self._disconnecting: set[str] = set()  # visitors whose disconnect is in progress
        self._pending_cleanup: set[str] = set()    # closed visitors whose runtime could not be removed yet
        self._cleaning: set[str] = set()       # visitors whose removal one thread is executing right now
        self._lock = threading.Lock()
        # Under the lock: only state checks, reservations and transitions.
        # Waiting for processes, the official logout and directory removal
        # always happen outside it.
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
            return {"available": True, "connected": False, "runtime": False, "detail": "not connected",
                    "busy": False, "disconnecting": visitor in self._disconnecting, "pending_cleanup": visitor in self._pending_cleanup}
        st = self.connector(visitor).status()
        login = self._logins.get(visitor)
        return {"available": True, "connected": st["connected"], "runtime": True, "account": st.get("account"),
                "detail": st.get("detail"), "login_state": login.state if login else None,
                "busy": visitor in self._active.values(), "disconnecting": visitor in self._disconnecting,
                "pending_cleanup": visitor in self._pending_cleanup}

    # -- sign-in --------------------------------------------------------------
    def _closing(self, visitor: str) -> bool:
        return visitor in self._disconnecting or visitor in self._pending_cleanup or visitor in self._cleaning

    def _login_running(self, visitor: str) -> bool:
        login = self._logins.get(visitor, "none")
        return login is None or (login != "none" and not login.finished)

    def _processes(self) -> int:
        return sum(1 for v in list(self._logins) if self._login_running(v)) + len(self._active)

    def login_start(self, visitor: str) -> dict:
        path = which(self.binary)
        if not path:
            raise VisitorError("Claude Code is not installed on this host.")
        with self._lock:
            if self._closing(visitor):
                raise VisitorError("This connection is being closed; wait for it to finish, then connect again.")
            if self._login_running(visitor):
                running = self._logins[visitor]
                return {"state": running.state if running else "starting", "already": True}
            if self._processes() >= self.max_processes:
                raise VisitorError("This host is at its process limit; try again in a moment.")
            self._logins[visitor] = None            # reserved: counts as a process from here on
        try:
            self.ensure(visitor)
            session = LoginSession([path, "auth", "login"], self.environment(visitor), self.home(visitor),
                                   on_state=lambda ev: self._event(visitor, "login", state=ev["state"]))
        except BaseException:
            with self._lock:
                self._logins.pop(visitor, None)
            raise
        with self._lock:
            self._logins[visitor] = session
        return {"state": session.state, "already": False}

    def login_read(self, visitor: str, since: int = 0) -> dict:
        session = self._logins.get(visitor)
        if session is None:
            raise VisitorError("No sign-in in progress.")
        if session.finished:
            self._on_process_end(visitor)
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

    def login_cancel(self, visitor: str, wait_seconds: float = 10.0) -> dict:
        """Signal, then confirm: SIGTERM, a wait, SIGKILL, a wait. The session
        leaves the registry only once it has actually ended."""
        with self._lock:
            session = self._logins.get(visitor)
        if not session:
            return {"cancelled": False}
        ended = session.finished or self._terminate(session, wait_seconds)
        if ended:
            with self._lock:
                if self._logins.get(visitor) is session:
                    del self._logins[visitor]
            self._event(visitor, "login", state="cancelled")
            self._on_process_end(visitor)
        return {"cancelled": True, "ended": ended}

    @staticmethod
    def _terminate(session: LoginSession, wait_seconds: float) -> bool:
        session.stop(signal.SIGTERM)
        if session.wait(wait_seconds):
            return True
        session.stop(signal.SIGKILL)
        return session.wait(max(2.0, wait_seconds))

    # -- running questions: one slot per job, held until that job ends ---------
    def acquire(self, visitor: str, job_id: str) -> None:
        with self._lock:                        # the check and the reservation cannot be split
            if self._closing(visitor):
                raise VisitorError("This connection is being closed; wait for it to finish, then connect again.")
            if self._processes() >= self.max_processes:
                raise VisitorError("This host is at its process limit; try again in a moment.")
            self._active[job_id] = visitor
        self.touch(visitor)

    def release(self, job_id: str) -> None:
        with self._lock:
            visitor = self._active.pop(job_id, None)
        if visitor:
            self._on_process_end(visitor)

    def _on_process_end(self, visitor: str) -> None:
        """A question or a sign-in ended: if the visitor is waiting for
        cleanup and nothing else runs, do it now, once."""
        if self._claim_cleanup(visitor):
            self._cleanup(visitor)

    def _claim_cleanup(self, visitor: str) -> bool:
        """The right to run removal for a pending visitor: granted to one
        thread at a time, and only when no process of theirs is alive."""
        with self._lock:
            if visitor not in self._pending_cleanup or visitor in self._cleaning:
                return False
            if any(v == visitor for v in self._active.values()) or self._login_running(visitor):
                return False
            self._cleaning.add(visitor)
            return True

    def running_jobs(self, visitor: str) -> list[str]:
        with self._lock:
            return [j for j, v in self._active.items() if v == visitor]

    # -- disconnect: refuse new work, stop, wait for termination, official logout, remove
    def disconnect(self, visitor: str, *, stop_running=None, wait_seconds: float = 30.0) -> dict:
        """Closing a connection in order: no new logins or questions for this
        visitor; cancel what runs (questions and a sign-in alike); wait until
        every process of theirs has actually ended, escalating to SIGKILL;
        then the official logout and the removal of the credential store.
        If a process outlives the wait, or removal fails, the visitor stays
        `pending_cleanup` and no deletion happens under a live process; the
        next process end or the periodic sweep retries, and the pending
        state is cleared only when removal has succeeded."""
        self._check(visitor)
        steps = {"stopped": False, "logged_out": None, "removed": False, "waited_for": 0, "pending_cleanup": False}
        with self._lock:
            self._disconnecting.add(visitor)
            login = self._logins.get(visitor)
        try:
            if stop_running is not None:
                steps["stopped"] = bool(stop_running())
            if login is not None:
                self._terminate(login, wait_seconds)
                with self._lock:
                    if login.finished and self._logins.get(visitor) is login:
                        del self._logins[visitor]
                self._event(visitor, "login", state="cancelled" if login.finished else "still_running")
            deadline = time.time() + wait_seconds
            while time.time() < deadline and (self.running_jobs(visitor) or self._login_alive(visitor)):
                time.sleep(0.1)
            steps["waited_for"] = len(self.running_jobs(visitor)) + (1 if self._login_alive(visitor) else 0)
            with self._lock:
                self._pending_cleanup.add(visitor)   # pending until removal succeeds, whatever happens next
            if steps["waited_for"]:
                steps["pending_cleanup"] = True
                self._event(visitor, "disconnect", **steps)
                return steps
        finally:
            with self._lock:
                self._disconnecting.discard(visitor)
        if self._claim_cleanup(visitor):
            steps.update(self._cleanup(visitor))
        steps["pending_cleanup"] = visitor in self._pending_cleanup
        self._event(visitor, "disconnect", **steps)
        return steps

    def _login_alive(self, visitor: str) -> bool:
        with self._lock:
            return self._login_running(visitor)

    def _cleanup(self, visitor: str) -> dict:
        """Removal, by the thread holding the cleanup right: official logout,
        then the directory. Success clears the pending state; failure keeps
        it for a retry."""
        steps = {"logged_out": None, "removed": False}
        try:
            steps.update(self._remove(visitor))
        finally:
            with self._lock:
                self._cleaning.discard(visitor)
                if steps["removed"]:
                    self._pending_cleanup.discard(visitor)
        self._event(visitor, "cleanup", **steps)
        return steps

    def finish_pending(self) -> list[str]:
        """Complete disconnects whose processes have since ended (one
        executor per visitor; a failed removal is retried next time)."""
        done = []
        for visitor in list(self._pending_cleanup):
            if self._claim_cleanup(visitor) and self._cleanup(visitor)["removed"]:
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
            try:
                shutil.rmtree(self.dir(visitor))
            except OSError:
                pass
            steps["removed"] = not self.dir(visitor).exists()
        else:
            steps["removed"] = True
        return steps

    # -- housekeeping ----------------------------------------------------------
    def sweep(self, now: float | None = None) -> list[str]:
        """Remove runtimes idle longer than idle_days (official logout first)."""
        now = time.time() if now is None else now
        removed = self.finish_pending()
        for d in list(self.base.iterdir()):
            if not d.is_dir() or not _ID.fullmatch(d.name):
                continue
            with self._lock:
                busy = any(v == d.name for v in self._active.values()) or self._login_running(d.name) or self._closing(d.name)
            if busy:
                continue
            if now - d.stat().st_mtime > self.idle_days * 86400:
                if self.disconnect(d.name)["removed"]:
                    removed.append(d.name)
        return removed

    # -- the periodic sweep, tied to the server's life ---------------------------
    def start_sweeper(self, interval_seconds: float) -> None:
        self._stop = threading.Event()

        def loop():
            while not self._stop.wait(interval_seconds):
                try:
                    self.sweep()
                except Exception:  # noqa: BLE001 — a sweep must never take the server down
                    pass
        self._sweeper = threading.Thread(target=loop, daemon=True, name="visitor-sweeper")
        self._sweeper.start()

    def stop_sweeper(self) -> None:
        stop = getattr(self, "_stop", None)
        if stop is not None:
            stop.set()
            self._sweeper.join(timeout=5)

    def count(self) -> int:
        return sum(1 for p in self.base.iterdir() if p.is_dir())
