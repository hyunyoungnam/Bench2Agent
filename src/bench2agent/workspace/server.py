"""The local browser workspace: a loopback HTTP API over the same conversation
service the terminal uses (Session.ask), plus the page that reads it.

Standard library only. One machine, one owner: the server binds to 127.0.0.1,
accepts only its own Host names, refuses cross-site Origins and requires a
token minted at launch on every /api call (the launcher puts it in the URL it
opens; the page keeps it for the tab). No account, no sign-in screen.

Questions run as jobs in background threads, apart from any HTTP request: a
closed tab or a dropped connection does not cancel one, a reload reattaches to
it, and the same request id never runs a question twice. Cancel and save follow
session.TurnControl, the rule shared with the terminal.
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from bench2agent import providers
from bench2agent.session import Session, TurnControl, conversations

STATIC = Path(__file__).with_name("static")
CHAT_ID = re.compile(r"[0-9a-f]{12}")
JOB_ID = re.compile(r"[0-9a-f]{16}")
TOKEN_HEADER = "X-Workspace-Token"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


class Busy(ValueError):
    pass


class Job:
    """One question in flight: its events, its state, its cancel handle."""

    def __init__(self, chat_id: str, question: str, request_id: str):
        self.id = secrets.token_hex(8)
        self.chat_id, self.question, self.request_id = chat_id, question, request_id
        self.control = TurnControl()
        self.events: list[dict] = []
        self.state = "running"            # running | saved | cancelled | failed
        self.error: str | None = None
        self.turn_index: int | None = None
        self.started = time.time()
        self.finished: float | None = None
        self.cond = threading.Condition()

    def emit(self, event: dict) -> None:
        try:
            json.dumps(event)
        except (TypeError, ValueError):
            event = {"t": str(event.get("t", "event"))}
        with self.cond:
            self.events.append({**event, "at": round(time.time() - self.started, 2)})
            self.cond.notify_all()

    def finish(self, state: str, error: str | None = None, turn_index: int | None = None) -> None:
        with self.cond:
            self.state, self.error, self.turn_index, self.finished = state, error, turn_index, time.time()
            self.cond.notify_all()

    def view(self, since: int = 0) -> dict:
        return {"job": self.id, "chat": self.chat_id, "question": self.question, "state": self.state,
                "error": self.error, "turn_index": self.turn_index, "events": self.events[since:], "next": len(self.events),
                "elapsed": round((self.finished or time.time()) - self.started, 1)}


class Workspace:
    """Conversations, connectors and jobs for one local owner."""

    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self.by_request: dict[tuple[str, str], Job] = {}
        self.pending: dict[str, Session] = {}          # new conversations, not saved until their first answer

    # -- state ---------------------------------------------------------------
    def state(self) -> dict:
        from bench2agent.core import revisions
        from bench2agent.core.benchmarks import BenchmarkStore
        from bench2agent.runtime import available_connectors
        revision = revisions.active_revision(self.root)
        data = BenchmarkStore(self.root, revision=revision).data() if revision else {"available": False}
        connectors = [{"name": c["name"], "label": c.get("label"), "connected": bool(c.get("connected"))}
                      for c in available_connectors() if c["name"] in ("claude", "codex")]
        return {"revision": revision, "available": bool(data.get("available")), "generated_at": data.get("generated_at"),
                "editions": len(data.get("editions") or {}), "connectors": connectors}

    def chats(self) -> list[dict]:
        saved = conversations(self.root)
        ids = {c["id"] for c in saved}
        fresh = [{"id": cid, "title": "New conversation", "connector": s.doc.get("connector"), "ts": s.doc.get("ts"), "turns": 0}
                 for cid, s in self.pending.items() if cid not in ids]
        return fresh + saved

    def _session(self, cid: str) -> Session:
        if not CHAT_ID.fullmatch(cid):
            raise ValueError("Invalid conversation id.")
        if cid in self.pending and not (self.root / "data/bench2agent/chats" / f"{cid}.json").exists():
            return self.pending[cid]
        self.pending.pop(cid, None)
        doc = Session(self.root, "anthropic", None, cid=cid)        # provider and model come from the file
        return doc

    def chat(self, cid: str) -> dict:
        s = self._session(cid)
        active = next((j for j in self.jobs.values() if j.chat_id == cid and j.state == "running"), None)
        turns = []
        for i, t in enumerate(s.doc.get("turns") or []):
            turns.append({"index": i, "q": t.get("q"), "segs": t.get("segs"), "verified": t.get("verified"),
                          "unverifiable": t.get("unverifiable"), "trail": t.get("trail"), "connector": t.get("connector"),
                          "on": t.get("on"), "has_tools": bool(t.get("tool_log"))})
        return {"id": s.doc["id"], "title": s.doc.get("title"), "connector": s.doc.get("connector"),
                "snapshot_id": s.doc.get("snapshot_id"), "turns": turns, "active_job": active.id if active else None}

    def tools(self, cid: str, index: int) -> list[dict]:
        s = self._session(cid)
        turns = s.doc.get("turns") or []
        if not 0 <= index < len(turns) or not turns[index].get("tool_log"):
            return []
        name = turns[index]["tool_log"]
        if not re.fullmatch(r"[0-9a-f]{12}-\d+\.jsonl", name):
            raise ValueError("Invalid tool log name.")
        path = self.root / "data/bench2agent/chats/toollogs" / name
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out.append({k: row.get(k) for k in ("name", "arguments", "result", "revision")})
        return out

    # -- conversations and questions ------------------------------------------
    def new_chat(self, connector: str, language: str = "auto") -> dict:
        if connector not in ("claude", "codex"):
            raise ValueError("Choose Claude Code or Codex.")
        provider = "anthropic" if connector == "claude" else "openai"
        model = providers.DEFAULT_MODELS["openai"] if connector == "codex" else None
        s = Session(self.root, provider, model, language=language if language in ("auto", "ko", "en") else "auto",
                    connector=connector)
        with self.lock:
            self.pending[s.doc["id"]] = s
        return {"id": s.doc["id"]}

    def ask(self, cid: str, question: str, request_id: str) -> Job:
        question = (question or "").strip()
        if not question or len(question) > 4000:
            raise ValueError("Ask a question of up to 4,000 characters.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id or ""):
            raise ValueError("Missing request id.")
        with self.lock:
            same = self.by_request.get((cid, request_id))
            if same is not None:
                return same                                    # a resend or a reload: the same question, once
            if any(j.chat_id == cid and j.state == "running" for j in self.jobs.values()):
                raise Busy("This conversation is answering a question. Wait for it or cancel it.")
            session = self._session(cid)                       # re-read: another terminal's turns are seen
            job = Job(cid, question, request_id)
            self.jobs[job.id] = job
            self.by_request[(cid, request_id)] = job
        threading.Thread(target=self._run, args=(job, session), daemon=True, name=f"job-{job.id}").start()
        return job

    def _run(self, job: Job, session: Session) -> None:
        try:
            session.ask(job.question, job.emit, control=job.control)
        except providers.Cancelled as exc:
            job.finish("cancelled", str(exc))
        except (providers.ProviderError, ValueError, OSError) as exc:
            job.finish("failed", str(exc))
        except Exception as exc:  # noqa: BLE001 — the job must end, whatever the connector raised
            job.finish("failed", f"{type(exc).__name__}: {exc}")
        else:
            with self.lock:
                self.pending.pop(job.chat_id, None)
            job.finish("saved", turn_index=len(session.doc["turns"]) - 1)

    def cancel(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.state != "running":
            return {"result": "finished", "state": job.state}
        return {"result": job.control.cancel(), "state": job.state}

    def wait(self, job_id: str, since: int, timeout: float = 20.0) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        with job.cond:
            job.cond.wait_for(lambda: len(job.events) > since or job.state != "running", timeout=timeout)
            return job.view(since)

    def shutdown(self) -> None:
        """Cancel what still runs: a CLI started in its own session would otherwise outlive the server."""
        running = [j for j in self.jobs.values() if j.state == "running"]
        for j in running:
            j.control.cancel()
        deadline = time.time() + 15
        for j in running:
            with j.cond:
                j.cond.wait_for(lambda: j.state != "running", timeout=max(0.0, deadline - time.time()))


class Handler(BaseHTTPRequestHandler):
    server_version = "workspace"
    sys_version = ""
    workspace: Workspace
    token: str
    port: int

    def log_message(self, fmt, *args):  # quiet: no request lines with tokens in the terminal
        return

    # -- guards --------------------------------------------------------------
    def _hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def _guard(self, api: bool) -> bool:
        if self.headers.get("Host") not in self._hosts():
            self._send(421, {"error": "Unexpected host."})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {"http://" + h for h in self._hosts()}:
            self._send(403, {"error": "Cross-site request refused."})
            return False
        if api and not hmac.compare_digest(self.headers.get(TOKEN_HEADER, ""), self.token):
            self._send(403, {"error": "Open the workspace from the address its launcher printed."})
            return False
        return True

    def _send(self, code: int, body, content_type: str = "application/json; charset=utf-8") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self) -> dict:
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise ValueError("Send JSON.")
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64_000:
            raise ValueError("Request too large.")
        body = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("Send a JSON object.")
        return body

    # -- routes --------------------------------------------------------------
    def do_OPTIONS(self):  # no CORS, ever
        self._send(405, {"error": "Method not allowed."})

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        if not path.startswith("/api/"):
            if not self._guard(api=False):
                return
            if path in ("/", "/index.html"):
                name = "index.html"
            elif path.startswith("/static/"):
                name = path[len("/static/"):]
            else:
                return self._send(404, {"error": "Not found."})
            file = (STATIC / name).resolve()
            if STATIC.resolve() not in file.parents or not file.is_file():
                return self._send(404, {"error": "Not found."})
            kind = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            return self._send(200, file.read_bytes(), kind + ("; charset=utf-8" if kind.startswith("text/") or kind.endswith("javascript") else ""))
        if not self._guard(api=True):
            return
        ws, q = self.workspace, parse_qs(url.query)
        try:
            if path == "/api/state":
                return self._send(200, ws.state())
            if path == "/api/chats":
                return self._send(200, {"chats": ws.chats()})
            m = re.fullmatch(r"/api/chats/([0-9a-f]{12})", path)
            if m:
                return self._send(200, ws.chat(m.group(1)))
            m = re.fullmatch(r"/api/chats/([0-9a-f]{12})/turns/(\d+)/tools", path)
            if m:
                return self._send(200, {"tools": ws.tools(m.group(1), int(m.group(2)))})
            m = re.fullmatch(r"/api/jobs/([0-9a-f]{16})", path)
            if m:
                since = int((q.get("since") or ["0"])[0])
                return self._send(200, ws.wait(m.group(1), max(0, since)))
            return self._send(404, {"error": "Not found."})
        except KeyError:
            return self._send(404, {"error": "Not found."})
        except FileNotFoundError:
            return self._send(404, {"error": "Not found."})
        except (ValueError, OSError) as exc:
            return self._send(400, {"error": str(exc)})

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self._guard(api=True):
            return
        ws = self.workspace
        try:
            body = self._json_body()
            if path == "/api/chats":
                return self._send(200, ws.new_chat(str(body.get("connector") or ""), str(body.get("language") or "auto")))
            m = re.fullmatch(r"/api/chats/([0-9a-f]{12})/ask", path)
            if m:
                job = ws.ask(m.group(1), str(body.get("question") or ""), str(body.get("request_id") or ""))
                return self._send(200, job.view())
            m = re.fullmatch(r"/api/jobs/([0-9a-f]{16})/cancel", path)
            if m:
                return self._send(200, ws.cancel(m.group(1)))
            return self._send(404, {"error": "Not found."})
        except Busy as exc:
            return self._send(409, {"error": str(exc)})
        except KeyError:
            return self._send(404, {"error": "Not found."})
        except (ValueError, OSError) as exc:
            return self._send(400, {"error": str(exc)})


def make_server(root: Path, port: int = 0, token: str | None = None) -> tuple[ThreadingHTTPServer, Workspace, str]:
    workspace = Workspace(root)
    token = token or secrets.token_urlsafe(24)
    handler = type("BoundHandler", (Handler,), {"workspace": workspace, "token": token, "port": 0})
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:
        if port == 0:
            raise
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)     # the preferred port is taken
    httpd.daemon_threads = True
    handler.port = httpd.server_address[1]
    return httpd, workspace, token


def open_browser(url: str) -> bool:
    """WSL first (the browser lives on Windows), then the platform default."""
    import os
    import shutil
    import subprocess
    import webbrowser
    try:
        if "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower():
            for cmd in (["wslview", url], ["cmd.exe", "/c", "start", "", url]):
                if shutil.which(cmd[0]):
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     cwd="/mnt/c" if os.path.isdir("/mnt/c") else None)
                    return True
    except OSError:
        pass
    try:
        return bool(webbrowser.open(url))
    except Exception:  # noqa: BLE001
        return False
