"""The web host's tool transport: the six tools over HTTP MCP, one revision
per conversation, chosen by the endpoint path and by nothing else."""
from __future__ import annotations

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from bench2agent.providers import ProviderError


class HttpMcpTools:
    """`specs()` and `call()` for `providers.generate`, over the Streamable
    HTTP endpoint of one revision (`.../revisions/<id>/mcp`). Each call is its
    own stateless MCP session, run on a private event loop, so it can be used
    from the worker thread that drives the model loop. `headers` carry the
    credential the endpoint requires (the host's internal token for its own
    mount); they are sent on every request, listing and calls alike, and
    never appear in results or errors."""

    def __init__(self, endpoint: str, headers: dict | None = None, timeout: float = 120.0):
        self.endpoint = endpoint
        self.headers = dict(headers or {})
        self.timeout = timeout
        self._specs = None
        self.calls = 0
        self.seconds = 0.0   # time spent inside tool transport, for the timing breakdown

    def _run(self, coro):
        return asyncio.run(asyncio.wait_for(coro, self.timeout))

    async def _session(self, work):
        async with create_mcp_http_client(headers=self.headers or None) as http:
            async with streamable_http_client(self.endpoint, http_client=http) as (read, write, *_):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await work(session)

    def specs(self) -> list[dict]:
        if self._specs is None:
            try:
                listed = self._run(self._session(lambda s: s.list_tools()))
            except Exception as exc:  # noqa: BLE001 — transport trouble, reported without internals
                raise ProviderError(f"Benchmark tools are unreachable at {self.endpoint}: {type(exc).__name__}") from None
            self._specs = [{"name": t.name, "description": t.description or "", "inputSchema": t.input_schema}
                           for t in listed.tools]
        return self._specs

    def call(self, name: str, arguments) -> dict:
        import time
        self.calls += 1
        started = time.perf_counter()
        try:
            result = self._run(self._session(lambda s: s.call_tool(name, arguments or {})))
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Benchmark tool call failed in transport: {type(exc).__name__}"}
        finally:
            self.seconds += time.perf_counter() - started
        text = "".join(getattr(c, "text", "") for c in result.content)
        try:
            value = json.loads(text)
        except ValueError:
            return {"error": "Benchmark tool returned no JSON object."}
        return value if isinstance(value, dict) else {"error": "Benchmark tool returned an unexpected shape."}
