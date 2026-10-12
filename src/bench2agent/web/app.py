"""The hosted conversation: a web API over the same tools, the same model
loop and the same verifier as the terminal, with users who own their
conversations and one data revision per conversation.

    GET  /                                   the page
    GET  /api/me                             who the token is, today's attempts and answers
    GET  /api/revisions                      installed revisions; which new conversations get
    GET  /api/conversations                  the caller's conversations
    POST /api/conversations                  new one, pinned to the active revision now
    GET  /api/conversations/{cid}            history, turns, verification marks, timing
    POST /api/conversations/{cid}/ask        {question} -> NDJSON: progress events, then the turn
    POST /api/conversations/{cid}/cancel     withdraw the running question (accepted only before saving starts)
    DELETE /api/conversations/{cid}
    /mcp-server/...                          the HTTP MCP server this host calls; internal token only

A web user arrives at the address, redeems an invite code once
(`POST /api/login` with the code and a display name; the operator makes codes
with `bench2agent web invite add`) and gets a browser session cookie
(HttpOnly, SameSite=Lax); from then on the page just works. Cookie-
authenticated state changes must also carry `X-Requested-With: Bench2Agent`,
which a cross-site form cannot add. API clients may instead send
`Authorization: Bearer <token>` from `bench2agent web user add`. Ownership
is checked on read, ask, cancel and delete alike: another user's
conversation does not exist.

A question is a job with one state at a time, changed only under its lock:
running -> cancelled, or running -> committing -> saved | failed. Cancel is
accepted only while running, and a job that has reached committing can no
longer be cancelled, so an accepted cancel is never followed by a save. The
job, not the browser connection, holds the conversation's running slot: a
dropped connection releases nothing until the job ends.

The daily limit counts attempts (the model was called), reserved in one
database transaction before the first model call, so two requests with one
attempt left cannot both pass. Each attempt records outcome, duration and
tool calls, never a credential.

The model loop runs in a worker thread with the conversation's revision bound
to it (`mcp.using`); its tools are reached over HTTP MCP at the endpoint of
that revision on the server this host mounts, with an internal bearer token
generated at startup and sent on every internal request (initialize, listing,
calls). The mount accepts nothing else, and the token is never logged or
shown. A conversation created on v1 keeps v1 after v2 is published; a missing
v1 is a recovery error, never an answer from v2.

Model credentials are deployment configuration (BENCH2AGENT_WEB_CREDENTIALS):
`service` means the operator configured a provider key on the server and the
host uses it for every user's questions, under the per-user attempt limits;
the key is never sent to a user. `user` means each question carries the
user's own provider key in `X-Model-Key`: it travels from the browser through
this backend to the model provider for that request and is neither stored
nor logged. Unset, the mode is `service` when the host environment holds a
provider key, else `user`. The operator's coding-CLI login is never used
for web users.
"""
from __future__ import annotations

import contextlib
import json
import os
import queue
import secrets
import threading
import time
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from bench2agent import providers
from bench2agent.core import chat, mcp, revisions
from bench2agent.core.benchmarks import BenchmarkStore
from bench2agent.core.mcp_http import HttpMcp
from bench2agent.core.policy import BENCHMARK_SYSTEM
from bench2agent.core.verify import Verifier
from bench2agent.web.mcp_client import HttpMcpTools
from bench2agent.web.store import WebStore

STATIC = Path(__file__).with_name("static")


class Settings:
    def __init__(self, root: Path, *, host: str = "127.0.0.1", port: int = 8780, mcp_url: str | None = None,
                 mcp_token: str | None = None, credentials: str | None = None, turns_per_day: int | None = None,
                 concurrent_per_user: int = 2):
        self.root = root
        self.host, self.port = host, port
        # the HTTP MCP this host calls: its own mount by default, or another service
        self.mcp_url = (mcp_url or os.environ.get("BENCH2AGENT_MCP_URL") or f"http://{host}:{port}/mcp-server").rstrip("/")
        # the credential for that service: ours is generated per process; an
        # external service's comes from configuration, kept apart from ours
        self.external_mcp_token = mcp_token or os.environ.get("BENCH2AGENT_MCP_TOKEN")
        configured = credentials or os.environ.get("BENCH2AGENT_WEB_CREDENTIALS")
        if configured is None:
            configured = "service" if any(os.environ.get(k) for k in providers.KEY_NAMES.values()) else "user"
        self.credentials = configured
        if self.credentials not in ("user", "service"):
            raise ValueError("BENCH2AGENT_WEB_CREDENTIALS must be user or service")
        self.cookie_secure = os.environ.get("BENCH2AGENT_WEB_SECURE_COOKIES", "").lower() in ("1", "true", "yes")
        self.turns_per_day = turns_per_day if turns_per_day is not None else int(os.environ.get("BENCH2AGENT_WEB_TURNS_PER_DAY", "100"))
        self.concurrent_per_user = concurrent_per_user


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class Login(BaseModel):
    invite: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=80)


class NewConversation(BaseModel):
    provider: str = Field(pattern="^(openai|anthropic)$")
    model: str | None = Field(default=None, max_length=120)
    language: str = Field(default="auto", pattern="^(auto|ko|en)$")


class Job:
    """One question in flight. `state` changes only under `lock`."""

    def __init__(self, user_id: str, attempt_id: int):
        self.user_id = user_id
        self.attempt_id = attempt_id
        self.lock = threading.Lock()
        self.state = "running"
        self.events: queue.Queue = queue.Queue()

    def cancel(self) -> bool:
        """Accepted only while running; after that the answer is being saved."""
        with self.lock:
            if self.state == "running":
                self.state = "cancelled"
                return True
            return False

    def cancelled(self) -> bool:
        return self.state == "cancelled"

    def commit(self) -> bool:
        """Move to committing unless a cancel was accepted first."""
        with self.lock:
            if self.state != "running":
                return False
            self.state = "committing"
            return True

    def finish(self, state: str) -> None:
        with self.lock:
            self.state = state


class InternalAuth:
    """Only the host itself (or whoever holds this process's internal token)
    reaches the mounted MCP server: every path under the mount, every
    method. The token is compared in constant time and never echoed."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        auth = next((v.decode() for k, v in scope.get("headers", []) if k == b"authorization"), "")
        offered = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not offered or not secrets.compare_digest(offered, self.token):
            body = json.dumps({"error": "The internal MCP mount accepts only the host's own token."}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def create_app(root: Path, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings(root)
    web = WebStore(root / "data/bench2agent/web.sqlite")
    http_mcp = HttpMcp(root, host=settings.host)
    internal_token = secrets.token_urlsafe(32)
    running: dict[str, Job] = {}
    running_lock = threading.Lock()

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with http_mcp.manager.run():
            yield

    app = FastAPI(title="Bench2Agent", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings, app.state.web, app.state.http_mcp = settings, web, http_mcp
    app.mount("/mcp-server", InternalAuth(http_mcp, internal_token))

    def mcp_headers() -> dict:
        own = settings.mcp_url.startswith(f"http://{settings.host}:{settings.port}")
        token = internal_token if own else settings.external_mcp_token
        return {"Authorization": "Bearer " + token} if token else {}

    # -- auth and ownership ----------------------------------------------------
    SESSION_COOKIE = "b2a_session"

    def current_user(request: Request, authorization: str | None = Header(default=None),
                     x_requested_with: str | None = Header(default=None),
                     b2a_session: str | None = Cookie(default=None)) -> dict:
        token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
        if token:
            user = web.authenticate(token)
            if user:
                return {**user, "auth": "token"}
            raise HTTPException(401, "This bearer token is not valid.")
        user = web.authenticate_session(b2a_session)
        if not user:
            raise HTTPException(401, "Sign in first (an invite code from the operator opens your session).")
        if request.method not in ("GET", "HEAD", "OPTIONS") and x_requested_with != "Bench2Agent":
            raise HTTPException(403, "State-changing requests from a browser session need X-Requested-With: Bench2Agent.")
        return {**user, "auth": "session"}

    def set_session(response: Response, session: str) -> None:
        response.set_cookie(SESSION_COOKIE, session, httponly=True, samesite="lax", secure=settings.cookie_secure,
                            max_age=30 * 24 * 3600, path="/")

    def owned(user: dict, cid: str, *, with_doc: bool = True) -> dict:
        if not cid.isalnum() or len(cid) > 32:
            raise HTTPException(404, "Conversation not found.")
        conv = web.conversation(user["user_id"], cid, with_doc=with_doc)
        if conv is None:
            raise HTTPException(404, "Conversation not found.")
        return conv

    def public(conv: dict) -> dict:
        return {k: v for k, v in conv.items() if k != "messages"}

    # -- routes -------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def page():
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/render.js")
    def render_js():
        return Response((STATIC / "render.js").read_text(encoding="utf-8"), media_type="application/javascript")

    @app.get("/api/app.js")
    def app_js():
        return Response((STATIC / "app.js").read_text(encoding="utf-8"), media_type="application/javascript")

    @app.post("/api/login")
    def login(body: Login, response: Response, x_requested_with: str | None = Header(default=None)):
        if x_requested_with != "Bench2Agent":
            raise HTTPException(403, "Sign-in needs X-Requested-With: Bench2Agent.")
        try:
            opened = web.redeem_invite(body.invite, body.name)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        set_session(response, opened["session"])
        return {"name": opened["name"]}

    @app.post("/api/logout")
    def logout(response: Response, user: dict = Depends(current_user), b2a_session: str | None = Cookie(default=None)):
        web.close_session(b2a_session)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"signed_out": True}

    @app.get("/api/me")
    def me(user: dict = Depends(current_user)):
        usage = web.usage_today(user["user_id"])
        return {"name": user["name"], "auth": user["auth"], "credentials": settings.credentials,
                "turns_per_day": settings.turns_per_day, "attempts_today": usage["attempts"], "answers_today": usage["answers"],
                "turns_today": usage["attempts"]}

    @app.get("/api/revisions")
    def list_revisions(user: dict = Depends(current_user)):
        try:
            active = revisions.active_revision(root)
            problem = None
        except revisions.RevisionError as exc:
            active, problem = None, str(exc)
        return {"active": active, "problem": problem,
                "revisions": [{k: r[k] for k in ("id", "active", "generated_at", "installed_at")} for r in revisions.list_revisions(root)]}

    @app.get("/api/conversations")
    def list_conversations(user: dict = Depends(current_user)):
        return {"conversations": web.conversations(user["user_id"])}

    @app.post("/api/conversations", status_code=201)
    def new_conversation(body: NewConversation, user: dict = Depends(current_user)):
        try:
            rid = revisions.active_revision(root)
        except revisions.RevisionError as exc:
            raise HTTPException(503, str(exc))
        if rid is None:
            raise HTTPException(503, "No data revision is installed on this host.")
        model = body.model or providers.DEFAULT_MODELS[body.provider]
        return public(web.create_conversation(user["user_id"], revision=rid, provider=body.provider, model=model, language=body.language))

    @app.get("/api/conversations/{cid}")
    def get_conversation(cid: str, user: dict = Depends(current_user)):
        conv = public(owned(user, cid))
        with running_lock:
            job = running.get(cid)
        conv["running"] = job.state if job else None
        return conv

    @app.delete("/api/conversations/{cid}", status_code=204)
    def delete_conversation(cid: str, user: dict = Depends(current_user)):
        owned(user, cid, with_doc=False)
        with running_lock:
            if cid in running:
                raise HTTPException(409, "A question is running in this conversation; cancel it first.")
        web.delete_conversation(user["user_id"], cid)
        return JSONResponse(status_code=204, content=None)

    @app.post("/api/conversations/{cid}/cancel")
    def cancel(cid: str, user: dict = Depends(current_user)):
        owned(user, cid, with_doc=False)
        with running_lock:
            job = running.get(cid)
        if job is None:
            return {"cancelled": False, "state": None, "message": "No question is running."}
        if job.cancel():
            return {"cancelled": True, "state": "cancelled", "message": "Cancelled; nothing will be saved."}
        return {"cancelled": False, "state": job.state,
                "message": "Too late to cancel: the answer passed verification and is being saved."}

    @app.post("/api/conversations/{cid}/ask")
    def ask(cid: str, body: Ask, user: dict = Depends(current_user), x_model_key: str | None = Header(default=None)):
        conv = owned(user, cid)
        if settings.credentials == "user" and not x_model_key:
            raise HTTPException(400, "This host uses your own model credential: send it as X-Model-Key for each question. "
                                     "It is bound to this request only and never stored.")
        with running_lock:
            if cid in running:
                raise HTTPException(409, "A question is already running in this conversation.")
            if sum(1 for j in running.values() if j.user_id == user["user_id"]) >= settings.concurrent_per_user:
                raise HTTPException(429, f"At most {settings.concurrent_per_user} questions at once.")
            attempt = web.reserve_attempt(user["user_id"], cid, body.question, settings.turns_per_day)
            if attempt is None:
                raise HTTPException(429, f"Daily limit of {settings.turns_per_day} attempts reached.")
            job = running[cid] = Job(user["user_id"], attempt)
        keys = {conv["provider"]: x_model_key} if settings.credentials == "user" else None

        def release():
            with running_lock:
                if running.get(cid) is job:
                    del running[cid]
        threading.Thread(target=_run_turn, args=(root, settings, web, user, conv, body.question, keys, job, mcp_headers(), release),
                         daemon=True).start()

        def stream():
            # the connection may drop; the job keeps its slot until it ends on its own
            while True:
                event = job.events.get()
                yield json.dumps(event, ensure_ascii=False) + "\n"
                if event.get("t") in ("turn", "error"):
                    break
        return StreamingResponse(stream(), media_type="application/x-ndjson")

    return app


def _run_turn(root: Path, settings: Settings, web: WebStore, user: dict, conv: dict, question: str, keys: dict | None,
              job: Job, headers: dict, release) -> None:
    """One question, start to finish, on the conversation's revision."""
    emit = job.events.put
    rid = conv["revision"]
    started = time.perf_counter()
    tools = None
    marks = {"loop": None, "verify": None}

    def breakdown() -> dict:
        tool_s = tools.seconds if tools else 0.0
        loop_s = marks["loop"] if marks["loop"] is not None else 0.0
        return {"duration_ms": int((time.perf_counter() - started) * 1000),
                "model_ms": int(max(0.0, loop_s - tool_s) * 1000), "tool_ms": int(tool_s * 1000),
                "verify_ms": int((marks["verify"] or 0.0) * 1000), "tool_calls": tools.calls if tools else 0}

    def finish(outcome: str, usage: dict | None = None):
        t = breakdown()
        web.finish_attempt(job.attempt_id, outcome, duration_ms=t["duration_ms"], tool_calls=t["tool_calls"], usage=usage, timing=t)
    try:
        try:
            revisions.paths_for(root, rid)
        except revisions.RevisionError as exc:
            raise providers.ProviderError(f"This conversation's data revision {rid} needs recovery: {exc}") from None
        store = BenchmarkStore(root, revision=rid)
        if not store.data()["available"]:
            raise providers.ProviderError(store.usage({}).get("error") or f"Revision {rid} is not served.")
        language = conv.get("language", "auto")
        system = BENCHMARK_SYSTEM + ("\nAnswer in the user's language." if language == "auto" else
                                    "\nWrite prose in " + {"ko": "Korean", "en": "English"}.get(language, language) + ".")
        tools = HttpMcpTools(f"{settings.mcp_url}/revisions/{rid}/mcp", headers=headers)
        # service mode leaves the process environment visible to the provider;
        # user mode binds the request's own key and nothing else
        bound = providers.using_credentials(keys) if keys is not None else contextlib.nullcontext()
        with mcp.using(store), bound:
            loop_started = time.perf_counter()
            answer = providers.generate(conv["provider"], conv["model"], system, conv["messages"], question, emit,
                                        tools=tools, cancelled=job.cancelled)
            marks["loop"] = time.perf_counter() - loop_started
            if job.cancelled():
                raise providers.Cancelled("The question was cancelled; nothing was saved.")
            verify_started = time.perf_counter()
            corpus = mcp.Store()
            segs, verified = chat.segment(answer["text"], corpus, Verifier(corpus), answer["trail"])
            marks["verify"] = time.perf_counter() - verify_started
        if not job.commit():                                     # a cancel accepted during verification wins
            raise providers.Cancelled("The question was cancelled; nothing was saved.")
        snapshot = store.data()
        turn = {"q": question, "segs": segs, "verified": verified, "trail": answer["trail"], "usage": answer["usage"],
                "timing": {"attempt": job.attempt_id, **breakdown()},
                "on": {"benchmarks": {k: snapshot.get(k) for k in ("snapshot_id", "revision", "generated_at", "source")}}}
        saved = web.append_turn(user["user_id"], conv["cid"], messages=answer["messages"], turn=turn,
                                expected_version=conv["version"])
        job.finish("saved")
        finish("saved", answer["usage"])
        emit({"t": "turn", "turn": turn, "conversation": {k: saved[k] for k in ("cid", "title", "revision", "updated_at", "version")}})
    except providers.Cancelled as exc:
        job.finish("cancelled")
        finish("cancelled")
        emit({"t": "error", "cancelled": True, "message": str(exc)})
    except (providers.ProviderError, ValueError) as exc:
        job.finish("failed")
        finish("failed")
        emit({"t": "error", "message": str(exc)})
    except Exception:  # noqa: BLE001 — never a traceback or a credential to the client
        job.finish("failed")
        finish("failed")
        emit({"t": "error", "message": "The question failed inside the host; nothing was saved."})
    finally:
        release()


def serve(root: Path, *, host: str = "127.0.0.1", port: int = 8780) -> int:
    import uvicorn
    settings = Settings(root, host=host, port=port)
    app = create_app(root, settings)
    print(f"Bench2Agent web on http://{host}:{port}/  (credentials: {settings.credentials}; "
          f"tools via {settings.mcp_url}; pinned MCP revision {app.state.http_mcp.pinned})", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0
