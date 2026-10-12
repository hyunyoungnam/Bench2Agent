"""A provider API key as a connector: Bench2Agent runs the model/tool loop
itself (`providers.generate`) against the pinned store. The key comes from
the environment (terminal, or a service-funded host) or is bound to the
request by the caller (`providers.using_credentials`); this module never
holds one."""
from __future__ import annotations

import json
import os

from bench2agent import providers
from bench2agent.core import mcp
from bench2agent.core.benchmarks import BenchmarkStore

from .base import Connector, ConnectorError, Run, register


class _RecordingTools(providers.LocalTools):
    """The local transport, recording every call with its result so the
    trail has the same shape as the MCP server's log."""

    def __init__(self, revision: str):
        self.revision = revision
        self.log: list[dict] = []

    def call(self, name, arguments):
        result = super().call(name, arguments)
        self.log.append({"name": name, "arguments": arguments or {}, "result": result, "revision": self.revision})
        return result


@register
class ApiKeyConnector(Connector):
    name = "api"
    label = "Provider API key"
    options = ("provider",)

    def __init__(self, provider: str = "anthropic"):
        if provider not in providers.KEY_NAMES:
            raise ConnectorError("provider must be openai or anthropic")
        self.provider = provider

    def status(self) -> dict:
        present = bool(providers.credential(self.provider))
        return {"connected": present, "account": None, "installed": True,
                "how": f"{self.provider} API key from the environment ({providers.KEY_NAMES[self.provider]}) or bound to the request",
                "detail": "key present" if present else "no key in the environment",
                "public_hosting": "The operator's or the user's own API key, billed to its owner; no third-party login involved."}

    def login(self) -> dict:
        return {"kind": "env", "variable": providers.KEY_NAMES[self.provider],
                "note": "Set the provider key in the environment (terminal) or send it with the request (web, user mode)."}

    def ask(self, *, root, revision, question, system, history, provider_session, emit, cancelled, tool_log, model=None):
        store = BenchmarkStore(root, revision=revision)
        if not store.data()["available"]:
            raise ConnectorError(store.usage({}).get("error") or f"Revision {revision} is not served.")
        tools = _RecordingTools(revision)
        model = model or provider_session or providers.DEFAULT_MODELS[self.provider]
        with mcp.using(store):
            try:
                answer = providers.generate(self.provider, model, system, history or [], question, emit, tools=tools, cancelled=cancelled)
            except providers.Cancelled:
                raise ConnectorError("cancelled") from None
            except providers.ProviderError as exc:
                raise ConnectorError(str(exc)) from None
        return Run(answer["text"], tools.log, provider_session=model, usage=answer["usage"], messages=answer["messages"])
