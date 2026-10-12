"""The web host's tool transport: the six tools over HTTP MCP, one revision
per conversation, chosen by the endpoint path and by nothing else."""
from __future__ import annotations

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from bench2agent.providers import ProviderError


class HttpMcpTools:
    """`specs()` and `call()` for `providers.generate`, over the Streamable
    HTTP endpoint of one revision (`.../revisions/<id>/mcp`). Each call is its
    own stateless MCP session, run on a private event loop, so it can be used
    from the worker thread that drives the model loop."""

    def __init__(self, endpoint: str, timeout: float = 120.0):
        self.endpoint = endpoint
        self.timeout = timeout
        self._specs = None

    def _run(self, coro):
        return asyncio.run(asyncio.wait_for(coro, self.timeout))

    async def _list(self):
        async with streamable_http_client(self.endpoint) as (read, write, *_):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.list_tools()

    async def _call(self, name: str, arguments):
        async with streamable_http_client(self.endpoint) as (read, write, *_):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, arguments or {})

    def specs(self) -> list[dict]:
        if self._specs is None:
            try:
                listed = self._run(self._list())
            except Exception as exc:  # noqa: BLE001 — transport trouble, reported without internals
                raise ProviderError(f"Benchmark tools are unreachable at {self.endpoint}: {type(exc).__name__}") from None
            self._specs = [{"name": t.name, "description": t.description or "", "inputSchema": t.input_schema}
                           for t in listed.tools]
        return self._specs

    def call(self, name: str, arguments) -> dict:
        try:
            result = self._run(self._call(name, arguments))
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Benchmark tool call failed in transport: {type(exc).__name__}"}
        text = "".join(getattr(c, "text", "") for c in result.content)
        try:
            value = json.loads(text)
        except ValueError:
            return {"error": "Benchmark tool returned no JSON object."}
        return value if isinstance(value, dict) else {"error": "Benchmark tool returned an unexpected shape."}
