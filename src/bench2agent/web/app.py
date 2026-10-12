"""The hosted conversation: a web API over the same tools, the same model
loop and the same verifier as the terminal, with users who own their
conversations and one data revision per conversation.

    GET  /                                   the page
    GET  /api/me                             who the token is
    GET  /api/revisions                      installed revisions; which new conversations get
    GET  /api/conversations                  the caller's conversations
    POST /api/conversations                  new one, pinned to the active revision now
    GET  /api/conversations/{cid}            history, turns, verification marks
    POST /api/conversations/{cid}/ask        {question} -> NDJSON: progress events, then the turn
    POST /api/conversations/{cid}/cancel     withdraw the running question; nothing is saved
    DELETE /api/conversations/{cid}
    /mcp-server/...                          the HTTP MCP server this host calls (same process)

Every route except the page needs `Authorization: Bearer <token>`; a token is
issued with `bench2agent web user add NAME`. Ownership is checked on read,
ask, cancel and delete alike: another user's conversation does not exist.

The model loop runs in a worker thread with the conversation's revision bound
to it (`mcp.using`) and its tools reached through the HTTP MCP endpoint of
that revision; figure recomputation and quote checks use the same revision.
A conversation created on v1 keeps v1 after v2 is published; a missing v1 is
a recovery error, never an answer from v2.

Model credentials are deployment configuration: BENCH2AGENT_WEB_CREDENTIALS
is `user` (default: each request carries `X-Model-Key`, bound to that request
only, never stored or logged) or `service` (the host process's environment
pays for everyone; use with quotas). The operator's coding-CLI login is never
used for web users.
"""
from __future__ import annotations

import contextlib
import json
import os
import queue
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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
                 credentials: str | None = None, turns_per_day: int | None = None, concurrent_per_user: int = 2):
        self.root = root
        self.host, self.port = host, port
        # the HTTP MCP this host calls: its own mount by default, or another service
        self.mcp_url = (mcp_url or os.environ.get("BENCH2AGENT_MCP_URL") or f"http://{host}:{port}/mcp-server").rstrip("/")
        self.credentials = credentials or os.environ.get("BENCH2AGENT_WEB_CREDENTIALS", "user")
        if self.credentials not in ("user", "service"):
            raise ValueError("BENCH2AGENT_WEB_CREDENTIALS must be user or service")
        self.turns_per_day = turns_per_day if turns_per_day is not None else int(os.environ.get("BENCH2AGENT_WEB_TURNS_PER_DAY", "100"))
        self.concurrent_per_user = concurrent_per_user
        self.max_question = 4000


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class NewConversation(BaseModel):
    provider: str = Field(pattern="^(openai|anthropic)$")
    model: str | None = Field(default=None, max_length=120)
    language: str = Field(default="auto", pattern="^(auto|ko|en)$")


class Running:
    """One question in flight, per conversation; cancellation is a flag the
    model loop polls between rounds."""

    def __init__(self):
        self.cancel = threading.Event()
        self.events: queue.Queue = queue.Queue()


def create_app(root: Path, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings(root)
    web = WebStore(root / "data/bench2agent/web.sqlite")
    http_mcp = HttpMcp(root, host=settings.host)
    running: dict[str, Running] = {}
    running_lock = threading.Lock()

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with http_mcp.manager.run():
            yield

    app = FastAPI(title="Bench2Agent", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings, app.state.web, app.state.http_mcp = settings, web, http_mcp
    app.mount("/mcp-server", http_mcp)

    # -- auth and ownership ----------------------------------------------------
    def current_user(authorization: str | None = Header(default=None)) -> dict:
        token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
        user = web.authenticate(token)
        if not user:
            raise HTTPException(401, "A valid bearer token is required (bench2agent web user add NAME issues one).")
        return user

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

    @app.get("/api/me")
    def me(user: dict = Depends(current_user)):
        return {"name": user["name"], "credentials": settings.credentials, "turns_today": web.turns_today(user["user_id"]),
                "turns_per_day": settings.turns_per_day}

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
        return public(owned(user, cid))

    @app.delete("/api/conversations/{cid}", status_code=204)
    def delete_conversation(cid: str, user: dict = Depends(current_user)):
        owned(user, cid, with_doc=False)
        web.delete_conversation(user["user_id"], cid)
        return JSONResponse(status_code=204, content=None)

    @app.post("/api/conversations/{cid}/cancel")
    def cancel(cid: str, user: dict = Depends(current_user)):
        owned(user, cid, with_doc=False)
        with running_lock:
            job = running.get(cid)
        if job is None:
            return {"cancelled": False, "running": False}
        job.cancel.set()
        return {"cancelled": True, "running": True}

    @app.post("/api/conversations/{cid}/ask")
    def ask(cid: str, body: Ask, request: Request, user: dict = Depends(current_user),
            x_model_key: str | None = Header(default=None)):
        conv = owned(user, cid)
        if settings.credentials == "user" and not x_model_key:
            raise HTTPException(400, "This host uses your own model credential: send it as X-Model-Key for each question. "
                                     "It is bound to this request only and never stored.")
        if web.turns_today(user["user_id"]) >= settings.turns_per_day:
            raise HTTPException(429, f"Daily limit of {settings.turns_per_day} questions reached.")
        with running_lock:
            if cid in running:
                raise HTTPException(409, "A question is already running in this conversation.")
            if sum(1 for c in running if c in {x["cid"] for x in web.conversations(user["user_id"])}) >= settings.concurrent_per_user:
                raise HTTPException(429, f"At most {settings.concurrent_per_user} questions at once.")
            job = running[cid] = Running()
        keys = {conv["provider"]: x_model_key} if settings.credentials == "user" else {}
        thread = threading.Thread(target=_run_turn, args=(root, settings, web, user, conv, body.question, keys, job), daemon=True)
        thread.start()

        def stream():
            try:
                while True:
                    event = job.events.get()
                    yield json.dumps(event, ensure_ascii=False) + "\n"
                    if event.get("t") in ("turn", "error"):
                        break
            finally:
                with running_lock:
                    running.pop(cid, None)
        return StreamingResponse(stream(), media_type="application/x-ndjson")

    return app


def _run_turn(root: Path, settings: Settings, web: WebStore, user: dict, conv: dict, question: str, keys: dict, job: Running) -> None:
    """One question, start to finish, on the conversation's revision."""
    emit = job.events.put
    rid = conv["revision"]
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
        tools = HttpMcpTools(f"{settings.mcp_url}/revisions/{rid}/mcp")
        with mcp.using(store), providers.using_credentials(keys):
            answer = providers.generate(conv["provider"], conv["model"], system, conv["messages"], question, emit,
                                        tools=tools, cancelled=job.cancel.is_set)
            if job.cancel.is_set():
                raise providers.Cancelled("The question was cancelled; nothing was saved.")
            corpus = mcp.Store()
            segs, verified = chat.segment(answer["text"], corpus, Verifier(corpus), answer["trail"])
        snapshot = store.data()
        turn = {"q": question, "segs": segs, "verified": verified, "trail": answer["trail"], "usage": answer["usage"],
                "on": {"benchmarks": {k: snapshot.get(k) for k in ("snapshot_id", "revision", "generated_at", "source")}}}
        saved = web.append_turn(user["user_id"], conv["cid"], messages=answer["messages"], turn=turn,
                                expected_updated_at=conv["updated_at"])
        web.log_turn(user["user_id"], conv["cid"], "saved", answer["usage"])
        emit({"t": "turn", "turn": turn, "conversation": {k: saved[k] for k in ("cid", "title", "revision", "updated_at")}})
    except providers.Cancelled as exc:
        web.log_turn(user["user_id"], conv["cid"], "cancelled")
        emit({"t": "error", "cancelled": True, "message": str(exc)})
    except (providers.ProviderError, ValueError) as exc:
        web.log_turn(user["user_id"], conv["cid"], "failed")
        emit({"t": "error", "message": str(exc)})
    except Exception:  # noqa: BLE001 — never a traceback or a credential to the client
        web.log_turn(user["user_id"], conv["cid"], "failed")
        emit({"t": "error", "message": "The question failed inside the host; nothing was saved."})


def serve(root: Path, *, host: str = "127.0.0.1", port: int = 8780) -> int:
    import uvicorn
    settings = Settings(root, host=host, port=port)
    app = create_app(root, settings)
    print(f"Bench2Agent web on http://{host}:{port}/  (credentials: {settings.credentials}; "
          f"tools via {settings.mcp_url}; pinned MCP revision {app.state.http_mcp.pinned})", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0
