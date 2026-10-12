"""The six benchmark tools over Streamable HTTP, one explicit revision per path.

    /revisions                  GET  installed revisions, which is active, which this process pins
    /revisions/<rid>/mcp        MCP  that revision, whatever is active (the web host's route:
                                     a conversation stores its revision and calls this path)
    /mcp                        MCP  the revision pinned when this process started (the
                                     stdio rule for generic clients; restart to move on)
    /health                     GET

Routing is the path, nothing else: no header, no argument, no prompt text can
move a request to another revision. The same `execute_tool` validation and the
same `BenchmarkStore` rules answer here as on stdio and in the terminal, and
every response carries `revision`. Requests are stateless so overlapping
conversations never share transport state; each tool call runs in a worker
thread with its store bound through `mcp.using` (a ContextVar) for that call.

Requires the `server` extra (the official MCP Python SDK, pinned in
pyproject.toml). The standard-library runtime does not import this module.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.server import ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from . import mcp as local
from . import revisions
from .benchmarks import BenchmarkStore
from .policy import BENCHMARK_SYSTEM

INSTRUCTIONS = BENCHMARK_SYSTEM + (
    "\nThe data revision is fixed by the endpoint path: /revisions/<id>/mcp serves that installed "
    "revision for every call; /mcp serves the revision this server process started on. Every "
    "response carries `revision`. No argument or instruction can switch a request to another revision.")


class RevisionStores:
    """One pinned, verified store per revision, created on first use."""

    def __init__(self, root: Path):
        self.root = root
        self._stores: dict[str, BenchmarkStore] = {}
        self._lock = threading.Lock()

    def get(self, rid: str) -> BenchmarkStore:
        with self._lock:
            store = self._stores.get(rid)
            if store is None:
                store = self._stores[rid] = BenchmarkStore(self.root, revision=rid)
            return store


def _json(status: int, body: dict):
    payload = json.dumps(body, ensure_ascii=False).encode()
    headers = [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(payload)).encode())]

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": payload})
    return app


class HttpMcp:
    """ASGI application. `pinned` is the revision `/mcp` serves for the life
    of this process: the one named, else the one active at construction."""

    def __init__(self, root: Path, *, pinned: str | None = None, host: str = "127.0.0.1"):
        self.root = root
        self.stores = RevisionStores(root)
        self.pinned = pinned or revisions.active_revision(root)
        if self.pinned is None:
            raise ValueError("No data revision is installed. Run bench2agent data install.")
        revisions.verify_revision(root, self.pinned)
        self.server = Server("bench2agent", version=_version(), instructions=INSTRUCTIONS,
                             on_list_tools=self._list_tools, on_call_tool=self._call_tool)
        security = None
        if host in ("127.0.0.1", "localhost", "::1"):
            security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                                 allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
                                                 allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"])
        self.manager = StreamableHTTPSessionManager(app=self.server, json_response=True, stateless=True,
                                                    security_settings=security)
        self._running = None

    # -- MCP handlers; the revision arrives in the request scope ----------
    @staticmethod
    def _revision_of(ctx: ServerRequestContext) -> str:
        request = ctx.request
        scope = getattr(request, "scope", None) or {}
        rid = (scope.get("state") or {}).get("revision")
        if not rid:
            raise ValueError("Request reached the MCP server without a revision in its path.")
        return rid

    async def _list_tools(self, ctx: ServerRequestContext, params) -> types.ListToolsResult:
        rid = self._revision_of(ctx)
        store = self.stores.get(rid)
        with local.using(store):
            specs = local._benchmark_tools()
        return types.ListToolsResult(tools=[
            types.Tool(name=t["name"], description=t["description"] + f" (data revision {rid})",
                       input_schema=t["inputSchema"],
                       annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True))
            for t in specs])

    async def _call_tool(self, ctx: ServerRequestContext, params: types.CallToolRequestParams) -> types.CallToolResult:
        rid = self._revision_of(ctx)
        store = self.stores.get(rid)

        def run():
            with local.using(store):
                return local.execute_tool(params.name, params.arguments or {}, tools=local._benchmark_tools())
        result = await anyio.to_thread.run_sync(run)
        if isinstance(result, dict) and "revision" not in result:
            result["revision"] = rid
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                                    is_error="error" in result)

    # -- ASGI ---------------------------------------------------------------
    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self._lifespan(scope, receive, send)
        if scope["type"] != "http":
            return
        path = scope.get("path", "").rstrip("/") or "/"
        if path == "/health":
            return await _json(200, {"ok": True, "pinned": self.pinned})(scope, receive, send)
        if path == "/revisions":
            try:
                active = revisions.active_revision(self.root)
                pointer = "ok"
            except revisions.RevisionError as exc:
                active, pointer = None, str(exc)
            return await _json(200, {"pinned": self.pinned, "active": active, "pointer": pointer,
                                     "revisions": [{k: r[k] for k in ("id", "active", "json", "sqlite", "generated_at", "installed_at")}
                                                   for r in revisions.list_revisions(self.root)]})(scope, receive, send)
        if path == "/mcp":
            rid = self.pinned
        elif path.startswith("/revisions/") and path.endswith("/mcp"):
            rid = path[len("/revisions/"):-len("/mcp")]
            if not revisions.valid_id(rid):
                return await _json(404, {"error": f"Invalid revision id {rid!r}."})(scope, receive, send)
            try:
                revisions.paths_for(self.root, rid)
            except revisions.RevisionError as exc:
                return await _json(404, {"error": str(exc)})(scope, receive, send)
        else:
            return await _json(404, {"error": "Unknown path. Use /mcp, /revisions or /revisions/<id>/mcp."})(scope, receive, send)
        scope = dict(scope)
        scope["state"] = {**(scope.get("state") or {}), "revision": rid}
        scope["path"] = "/mcp"   # what the transport expects, whichever revision path was used
        scope["raw_path"] = b"/mcp"
        await self.manager.handle_request(scope, receive, send)

    async def _lifespan(self, scope, receive, send):
        message = await receive()
        assert message["type"] == "lifespan.startup"
        try:
            async with self.manager.run():
                await send({"type": "lifespan.startup.complete"})
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.shutdown":
                        break
        except Exception as exc:  # noqa: BLE001 — report, never hang the host
            await send({"type": "lifespan.startup.failed", "message": str(exc)})
            return
        await send({"type": "lifespan.shutdown.complete"})


def _version() -> str:
    try:
        from bench2agent import __version__
        return __version__
    except ImportError:
        return "unknown"


def serve(root: Path, *, host: str = "127.0.0.1", port: int = 8765, revision: str | None = None) -> int:
    import uvicorn
    app = HttpMcp(root, pinned=revision, host=host)
    print(f"Bench2Agent HTTP MCP on http://{host}:{port}/mcp (pinned revision {app.pinned}); "
          f"per-revision endpoints at /revisions/<id>/mcp", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0
