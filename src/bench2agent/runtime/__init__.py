"""The execution layer the web and the terminal share.

A *connector* is one way to run a question: the official Claude Code binary
with the user's own Claude login, the official Codex CLI with the user's own
ChatGPT login, or a provider API key. Every connector does the same six
things and nothing else: report its connection, say how to sign in, run one
question on one data revision while emitting progress, hand back the
answer text with the tool trail, accept cancellation, and disconnect.

What the layer guarantees, whichever connector runs:

- The tools are Bench2Agent's own MCP server, pinned to the conversation's
  data revision, so every count comes from that revision. When the official
  CLI owns the model/tool loop, Bench2Agent does not run a second loop; it
  records the loop. The MCP server writes every call (name, arguments,
  result, revision) to a per-run log, and the connector returns that log as
  the trail, so the answer can be checked against what the tools actually
  returned, not only against the final text.
- Credentials are the provider's business. Claude Code and Codex keep their
  own logins in their own configuration directories (per account directory
  when the caller asks); Bench2Agent never reads, copies or forwards them.
- Nothing here knows about HTTP or terminals: callers pass `emit` for
  progress and `cancelled` for withdrawal.
"""
from __future__ import annotations

from .base import Connector, ConnectorError, Run, available_connectors, connector
from .cli_agents import ClaudeCodeConnector, CodexConnector
from .api_key import ApiKeyConnector

__all__ = ["Connector", "ConnectorError", "Run", "ApiKeyConnector", "ClaudeCodeConnector", "CodexConnector",
           "available_connectors", "connector"]
