"""The official agent CLIs as connectors: Claude Code and Codex, unmodified,
with the user's own login. Each runs the model/tool loop itself; Bench2Agent
gives it the pinned MCP server, records the loop and verifies the result.

Per-account isolation: `config_dir` (Claude: CLAUDE_CONFIG_DIR; Codex:
CODEX_HOME) keeps one user's login, sessions and settings apart from
another's. Bench2Agent never opens, copies or forwards what is in there.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

from .base import (Connector, ConnectorError, Run, check_trail, mcp_environment, mcp_stdio_command, read_tool_log,
                   register, transcript, which)

CLAUDE_PUBLIC_HOSTING = (
    "Documented (code.claude.com/docs/en/legal-and-compliance): a platform may host the unmodified Claude Code "
    "binary where each end user signs in through Anthropic's own flow with their own subscription or key; the "
    "platform may not intermediate, store or route through Claude.ai credentials, and must agree to the Commercial "
    "Terms. Not executed by Bench2Agent yet: a hosted deployment needs one isolated Claude Code runtime per user.")
CODEX_PUBLIC_HOSTING = (
    "Documented (developers.openai.com/siwc/token-sharing-open-source): ChatGPT plan usage through Sign in with "
    "ChatGPT is for open-source and locally hosted apps; a paid or remotely hosted app needs OpenAI's interest form. "
    "The Codex CLI's own ChatGPT login is for the person running it. Not executed by Bench2Agent for remote users.")


def _summ(args: dict) -> str:
    for k in ("query", "topic", "benchmark", "venue", "edition"):
        if k in (args or {}):
            return str(args[k])[:60]
    return ""


def _failure(code: int, stderr: str) -> str:
    message = stderr.lower()
    if "not logged in" in message or "authentication" in message or "unauthorized" in message or "login" in message:
        return "The agent CLI is not signed in. Sign in with the CLI's own login, then try again."
    if "rate limit" in message or "usage limit" in message:
        return "The agent CLI reports a usage limit for this account."
    return f"The agent CLI exited with status {code}."


# What a visitor's isolated CLI process may inherit from the host: the path
# to the binaries and the TLS/proxy settings the network needs. Never a
# provider key, never the operator's login, never the host's HOME.
ISOLATED_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "TZ", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR",
                          "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
                          "https_proxy", "http_proxy", "no_proxy")


def isolated_environment(home: Path) -> dict:
    env = {k: os.environ[k] for k in ISOLATED_ENV_ALLOWLIST if k in os.environ}
    env.update({"HOME": str(home), "TERM": "dumb", "NO_COLOR": "1"})
    return env


class _CliConnector(Connector):
    owns_loop = True
    binary = ""
    options = ("config_dir",)

    def __init__(self, config_dir: str | None = None, env: dict | None = None, cwd: str | None = None):
        """`env` given: the process gets exactly that environment (a hosted
        visitor's isolated one) plus the connector's own variables; absent:
        the terminal user's own environment."""
        self.config_dir = str(Path(config_dir).expanduser()) if config_dir else None
        self.base_env = dict(env) if env is not None else None
        self.cwd = Path(cwd) if cwd else None

    def _env(self, extra: dict | None = None) -> dict:
        env = dict(self.base_env) if self.base_env is not None else dict(os.environ)
        env.update(extra or {})
        return env

    def installed(self) -> str | None:
        return which(self.binary)

    def _run(self, cmd: list[str], *, cwd: Path, emit, cancelled, parse) -> tuple[str | None, str | None, str | None]:
        """Run the CLI, feeding each JSON line to `parse(ev)`, which returns
        (text, session, failure) updates. Cancellation terminates the process
        group; a terminated run has no answer."""
        text = session = failed = None
        errors: deque = deque(maxlen=12)
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                stdin=subprocess.DEVNULL, env=self._env(self.extra_env()), start_new_session=True)

        def kill():
            # the CLI runs in its own session, so a Ctrl+C in the terminal does
            # not reach it; cancellation of any kind must end it explicitly
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            def drain():
                for line in proc.stderr:
                    errors.append(line[-1000:])
            threading.Thread(target=drain, daemon=True).start()

            def watch():
                while proc.poll() is None:
                    if cancelled():
                        kill()
                        return
                    threading.Event().wait(0.2)
            threading.Thread(target=watch, daemon=True).start()
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t, s, f = parse(ev, emit)
                text = t if t is not None else text
                session = s or session
                failed = f or failed
            code = proc.wait()
        except BaseException as exc:          # KeyboardInterrupt in a terminal, or any abort
            kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if isinstance(exc, KeyboardInterrupt):
                raise ConnectorError("cancelled") from None
            raise
        finally:
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except OSError:
                    pass
        if cancelled():
            raise ConnectorError("cancelled")
        if code and not failed:
            failed = _failure(code, "".join(errors))
        return text, session, failed

    def extra_env(self) -> dict:
        return {}


@register
class ClaudeCodeConnector(_CliConnector):
    name = "claude"
    label = "Claude Code (your Claude login)"
    binary = "claude"

    def extra_env(self) -> dict:
        return {"CLAUDE_CONFIG_DIR": self.config_dir} if self.config_dir else {}

    def status(self) -> dict:
        path = self.installed()
        base = {"how": "the official Claude Code CLI, signed in with its own /login" + (f" (config {self.config_dir})" if self.config_dir else ""),
                "public_hosting": CLAUDE_PUBLIC_HOSTING, "installed": bool(path)}
        if not path:
            return {**base, "connected": False, "account": None, "detail": "claude is not installed on this host."}
        try:
            out = subprocess.run([path, "auth", "status", "--json"], capture_output=True, text=True, timeout=20,
                                 env=self._env(self.extra_env()))
            info = json.loads(out.stdout or "{}")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {**base, "connected": False, "account": None, "detail": "claude did not report its login state."}
        connected = bool(info.get("loggedIn"))
        return {**base, "connected": connected, "account": info.get("email") or info.get("authMethod"),
                "detail": ("signed in via " + str(info.get("authMethod"))) if connected else "not signed in"}

    def login(self) -> dict:
        return {"kind": "command", "command": "claude /login" if not self.config_dir else f"CLAUDE_CONFIG_DIR={self.config_dir} claude",
                "note": "Sign-in completes in Anthropic's own browser flow; Bench2Agent never sees the credential."}

    def ask(self, *, root, revision, question, system, history, provider_session, emit, cancelled, tool_log):
        path = self.installed()
        if not path:
            raise ConnectorError("claude is not installed on this host.")
        mcp = {"mcpServers": {"bench2agent": {"command": mcp_stdio_command(root, revision)[0],
                                              "args": mcp_stdio_command(root, revision)[1:],
                                              "env": mcp_environment(root, tool_log)}}}
        prompt = question if provider_session else transcript(history) + question
        # --tools "" removes every built-in tool (shell, files, web); the MCP
        # server's six read-only tools are the only ones left, and
        # --strict-mcp-config keeps any other MCP configuration out
        cmd = [path, "-p", prompt, "--output-format", "stream-json", "--verbose", "--max-turns", "12",
               "--tools", "", "--mcp-config", json.dumps(mcp), "--strict-mcp-config",
               "--allowedTools", "mcp__bench2agent", "--append-system-prompt", system]
        if provider_session:
            cmd += ["--resume", provider_session]
        seen: list[dict] = []

        def parse(ev, emit):
            if ev.get("type") == "assistant":
                for c in (ev.get("message") or {}).get("content", []):
                    if c.get("type") == "tool_use" and str(c.get("name", "")).startswith("mcp__bench2agent__"):
                        name = c["name"].split("__")[-1]
                        seen.append({"name": name, "arguments": c.get("input") or {}})
                        emit({"t": "tool", "name": name, "arguments": c.get("input") or {}})
            elif ev.get("type") == "result":
                return ev.get("result") or "", ev.get("session_id"), ((ev.get("result") or "agent failed")[:300] if ev.get("is_error") else None)
            return None, None, None
        text, session, failed = self._run(cmd, cwd=self.cwd or root, emit=emit, cancelled=cancelled, parse=parse)
        if failed or text is None:
            raise ConnectorError(failed or "Claude Code returned no answer.")
        log, damaged = read_tool_log(tool_log)
        return Run(text, log, provider_session=session, unverifiable=check_trail(log, damaged, seen, revision))


@register
class CodexConnector(_CliConnector):
    name = "codex"
    label = "Codex (your ChatGPT login)"
    binary = "codex"

    def extra_env(self) -> dict:
        return {"CODEX_HOME": self.config_dir} if self.config_dir else {}

    def status(self) -> dict:
        path = self.installed()
        base = {"how": "the official Codex CLI, signed in with its own `codex login`" + (f" (CODEX_HOME {self.config_dir})" if self.config_dir else ""),
                "public_hosting": CODEX_PUBLIC_HOSTING, "installed": bool(path)}
        if not path:
            return {**base, "connected": False, "account": None, "detail": "codex is not installed on this host."}
        try:
            out = subprocess.run([path, "login", "status"], capture_output=True, text=True, timeout=20, env=self._env(self.extra_env()))
        except (OSError, subprocess.TimeoutExpired):
            return {**base, "connected": False, "account": None, "detail": "codex did not report its login state."}
        text = (out.stdout + out.stderr).strip()
        connected = out.returncode == 0 and "logged in" in text.lower() and "not logged in" not in text.lower()
        return {**base, "connected": connected, "account": re.sub(r"^logged in (using|with)\s*", "", text, flags=re.I)[:80] if connected else None,
                "detail": text[:120]}

    def login(self) -> dict:
        return {"kind": "command", "command": "codex login" if not self.config_dir else f"CODEX_HOME={self.config_dir} codex login",
                "note": "Sign-in completes in OpenAI's own browser flow; Bench2Agent never sees the credential."}

    def ask(self, *, root, revision, question, system, history, provider_session, emit, cancelled, tool_log):
        path = self.installed()
        if not path:
            raise ConnectorError("codex is not installed on this host.")
        command = mcp_stdio_command(root, revision)
        env = mcp_environment(root, tool_log)
        values = {"mcp_servers.bench2agent.command": command[0], "mcp_servers.bench2agent.args": command[1:],
                  "mcp_servers.bench2agent.env": env, "mcp_servers.bench2agent.default_tools_approval_mode": "writes",
                  "sandbox_mode": "read-only"}
        cmd = [path, "exec"]
        if provider_session:
            cmd += ["resume", provider_session]
        cmd += ["--json", "--skip-git-repo-check"]
        for key, value in values.items():
            literal = ("{ " + ", ".join(k + " = " + json.dumps(v) for k, v in value.items()) + " }") if isinstance(value, dict) else json.dumps(value)
            cmd += ["-c", key + "=" + literal]
        cmd.append(system + "\n\n" + (transcript(history) if not provider_session else "") + "USER QUESTION:\n" + question)
        seen: list[dict] = []

        def parse(ev, emit):
            ty = ev.get("type") or ""
            if ty == "thread.started":
                return None, ev.get("thread_id"), None
            if ty == "turn.failed":
                return None, None, str(ev.get("error") or "codex turn failed")[:300]
            if ty in ("item.started", "item.completed"):
                it = ev.get("item") or {}
                ity = it.get("type") or ""
                if "mcp" in ity and ty == "item.started":
                    name = (it.get("tool") or it.get("name") or "tool").split("__")[-1]
                    args = it.get("arguments") if isinstance(it.get("arguments"), dict) else {}
                    seen.append({"name": name, "arguments": args})
                    emit({"t": "tool", "name": name, "arguments": args})
                elif ity == "agent_message" and ty == "item.completed":
                    return it.get("text") or "", None, None
            return None, None, None
        text, session, failed = self._run(cmd, cwd=self.cwd or root, emit=emit, cancelled=cancelled, parse=parse)
        if failed or text is None:
            raise ConnectorError(failed or "Codex returned no answer.")
        log, damaged = read_tool_log(tool_log)
        return Run(text, log, provider_session=session, unverifiable=check_trail(log, damaged, seen, revision))
