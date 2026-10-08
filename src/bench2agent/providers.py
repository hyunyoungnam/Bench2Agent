"""Small stdlib adapters for OpenAI Responses and Anthropic Messages.

Only the shared read-only benchmark tools are exposed. Model credentials are
read from the environment and never written to settings or conversations.
"""
from __future__ import annotations

import copy
import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_MODELS = {"openai": "gpt-6.1-sol", "anthropic": "claude-opus-5-5"}
KEY_NAMES = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
ENDPOINTS = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1"}
MAX_ROUNDS = 12


class ProviderError(RuntimeError):
    pass


def post(provider: str, path: str, payload: dict) -> dict:
    key = os.environ.get(KEY_NAMES[provider], "")
    if not key:
        raise ProviderError(f"Set {KEY_NAMES[provider]} or run bench2agent init.")
    base = os.environ.get("BENCH2AGENT_" + provider.upper() + "_BASE_URL", ENDPOINTS[provider]).rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ProviderError("Model endpoint must use HTTPS (HTTP is allowed for localhost).")
    from . import __version__
    headers = {"Content-Type": "application/json", "User-Agent": "Bench2Agent/" + __version__}
    if provider == "openai":
        headers["Authorization"] = "Bearer " + key
    else:
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(), headers=headers)
    try:
        # Current Claude models think before answering; a long answer with
        # several tool rounds can take minutes.
        with urllib.request.urlopen(req, timeout=300) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Providers and proxies can echo headers or keys in error bodies.
        # Return a useful status class without displaying the raw response.
        labels = {401: "API key rejected", 403: "access denied", 404: "model or endpoint unavailable",
                  429: "rate or account limit reached"}
        raise ProviderError(f"{provider}: {labels.get(exc.code, 'request failed')} (HTTP {exc.code}).") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise ProviderError(f"{provider}: connection failed or invalid JSON response.") from None
    if not isinstance(result, dict):
        raise ProviderError(f"{provider}: invalid response object.")
    return result


def tool_specs() -> list[dict]:
    from bench2agent.core.mcp import _benchmark_tools
    # Resolve bound methods for each new turn (also used by MCP tools/list).
    return _benchmark_tools()


def call_tool(name: str, arguments, trail: list, emit) -> dict:
    from bench2agent.core.mcp import execute_tool
    emit({"t": "tool", "name": name, "arguments": arguments})
    trail.append({"name": name, "arg": json.dumps(arguments, sort_keys=True, ensure_ascii=False)})
    return execute_tool(name, arguments, tools=tool_specs())


def generate(provider: str, model: str, system: str, history: list, question: str, emit) -> dict:
    """Return an answer plus complete protocol history; commit only on success.

    Replaying OpenAI output preserves reasoning/encrypted reasoning items.
    Claude tool_result blocks immediately follow their assistant tool_use.
    No partial answer containing unverified quotes is displayed.
    """
    messages = copy.deepcopy(history)
    messages.append({"role": "user", "content": question})
    trail: list = []
    tools = tool_specs()
    usage: dict = {}
    for round_index in range(MAX_ROUNDS):
        if provider == "openai":
            definitions = [{"type": "function", "name": t["name"], "description": t["description"],
                            "parameters": t["inputSchema"], "strict": False} for t in tools]
            payload = {
                "model": model, "instructions": system, "input": messages, "tools": definitions,
                "store": False, "include": ["reasoning.encrypted_content"]}
            if round_index == 0:
                payload["tool_choice"] = {"type": "function", "name": "benchmark_scope"}
            result = post(provider, "/responses", payload)
            if result.get("status") in {"failed", "incomplete", "cancelled"} or result.get("error"):
                raise ProviderError("OpenAI did not complete the answer; it has not been saved.")
            output = result.get("output") or []
            messages.extend(output)
            calls = [item for item in output if item.get("type") == "function_call"]
            text = "\n".join(block.get("text", "") for item in output
                             if item.get("type") == "message" for block in item.get("content", [])
                             if block.get("type") == "output_text")
            for call in calls:
                try:
                    args = json.loads(call.get("arguments") or "{}")
                except (ValueError, TypeError):
                    args = None
                value = call_tool(call.get("name", ""), args, trail, emit)
                messages.append({"type": "function_call_output", "call_id": call["call_id"],
                                 "output": json.dumps(value, ensure_ascii=False)})
        elif provider == "anthropic":
            definitions = [{"name": t["name"], "description": t["description"],
                            "input_schema": t["inputSchema"]} for t in tools]
            # No forced tool_choice: Claude Opus 5.5 / Sonnet 5.5 / Fable 5.1
            # reject {"type": "tool"} with a 400. The system prompt already
            # tells the model to call benchmark_scope first. 16k output tokens
            # leaves room for a long answer with its quotes; 4k was cut off.
            payload = {
                "model": model, "system": system, "messages": messages, "tools": definitions,
                "max_tokens": 16000}
            result = post(provider, "/messages", payload)
            if result.get("stop_reason") in {"max_tokens", "refusal", "pause_turn"} or result.get("error"):
                raise ProviderError("Anthropic did not complete the answer; it has not been saved.")
            output = result.get("content") or []
            messages.append({"role": "assistant", "content": output})
            calls = [item for item in output if item.get("type") == "tool_use"]
            text = "\n".join(item.get("text", "") for item in output if item.get("type") == "text")
            results = []
            for call in calls:
                value = call_tool(call.get("name", ""), call.get("input"), trail, emit)
                results.append({"type": "tool_result", "tool_use_id": call["id"],
                                "content": json.dumps(value, ensure_ascii=False), "is_error": "error" in value})
            if results:
                messages.append({"role": "user", "content": results})
        else:
            raise ProviderError("Choose openai or anthropic.")
        for key, value in (result.get("usage") or {}).items():
            if isinstance(value, (int, float)):
                usage[key] = usage.get(key, 0) + value
        if not calls:
            if not text.strip():
                raise ProviderError("The model returned no answer.")
            return {"text": text, "messages": messages, "trail": trail, "usage": usage}
    raise ProviderError("Tool-call limit reached; the unfinished answer has not been saved.")
