"""The shared execution layer with stub CLIs standing in for Claude Code and
Codex: the pinned MCP server is what the CLI is given, the MCP server's own
log is the trail, cancellation kills the CLI, and the API-key connector
records its calls in the same shape. No real model is called."""
import json
import os
import stat
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bench2agent import data as data_module, providers
from bench2agent.runtime import ConnectorError, available_connectors, connector
from bench2agent.runtime import cli_agents

try:
    from test_revisions import QUOTE_V1, snapshot
except ImportError:
    from tests.test_revisions import QUOTE_V1, snapshot

# A stand-in for `claude -p ... --output-format stream-json`: it reads the
# --mcp-config it was given, really starts that MCP server, calls one tool
# over stdio (so the server writes the tool log), and prints the events the
# real CLI prints. `SLEEP` lets the cancel test catch it mid-run.
STUB_CLAUDE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, subprocess, sys, time
    args = sys.argv[1:]
    if args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "stub", "email": "stub@example.org"})); sys.exit(0)
    cfg = json.loads(args[args.index("--mcp-config") + 1])["mcpServers"]["bench2agent"]
    if os.environ.get("STUB_SLEEP"):
        time.sleep(float(os.environ["STUB_SLEEP"]))
    env = dict(os.environ); env.update(cfg["env"])
    req = "\\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "benchmark_usage", "arguments": {"topic": "robotics"}}}]) + "\\n"
    out = subprocess.run([cfg["command"], *cfg["args"]], input=req, capture_output=True, text=True, env=env)
    result = json.loads(json.loads(out.stdout.splitlines()[-1])["result"]["content"][0]["text"])
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__bench2agent__benchmark_usage", "input": {"topic": "robotics"}}]}}))
    n = result["results"][0]["papers"]
    text = "Toy is used by ⟦benchmark_usage:{\\"topic\\":\\"robotics\\"}|%d paper⟧ ⟦arxiv:2501.00001|__QUOTE__⟧" % n
    print(json.dumps({"type": "result", "result": text, "session_id": "sess-stub-1", "is_error": False}))
''').replace("__QUOTE__", QUOTE_V1)

STUB_CODEX = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, subprocess, sys
    args = sys.argv[1:]
    if args[:2] == ["login", "status"]:
        print("Logged in using ChatGPT"); sys.exit(0)
    cfg = {}
    for i, a in enumerate(args):
        if a == "-c" and args[i + 1].startswith("mcp_servers.bench2agent."):
            key, _, value = args[i + 1].partition("=")
            cfg[key.rsplit(".", 1)[1]] = value
    command = json.loads(cfg["command"]); margs = json.loads(cfg["args"])
    env = dict(os.environ)
    for part in cfg["env"].strip("{} ").split(", "):
        k, _, v = part.partition(" = "); env[k.strip()] = json.loads(v)
    req = "\\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "benchmark_scope", "arguments": {}}}]) + "\\n"
    out = subprocess.run([command, *margs], input=req, capture_output=True, text=True, env=env)
    print(json.dumps({"type": "thread.started", "thread_id": "thread-stub-1"}))
    print(json.dumps({"type": "item.started", "item": {"type": "mcp_tool_call", "tool": "mcp__bench2agent__benchmark_scope", "arguments": {}}}))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Scope checked."}}))
''')


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "v1.json").write_text(json.dumps(snapshot("v1", QUOTE_V1)))
        data_module.install(self.root, file=str(self.root / "v1.json"))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name, body in (("claude", STUB_CLAUDE), ("codex", STUB_CODEX)):
            p = self.bin / name
            p.write_text(body)
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        self.env = patch.dict(os.environ, {"PATH": f"{self.bin}:{os.environ['PATH']}"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.log = self.root / "tool.log"

    def ask(self, name, question="q", cancelled=lambda: False, **kw):
        events = []
        run = connector(name, **kw).ask(root=self.root, revision="v1", question=question, system="sys", history=None,
                                        provider_session=None, emit=events.append, cancelled=cancelled, tool_log=self.log)
        return run, events

    def test_status_reports_both_clis_and_the_api_key(self):
        statuses = {c["name"]: c for c in available_connectors()}
        self.assertTrue(statuses["claude"]["connected"])
        self.assertEqual(statuses["claude"]["account"], "stub@example.org")
        self.assertTrue(statuses["codex"]["connected"])
        self.assertFalse(statuses["api"]["connected"])
        for c in statuses.values():
            self.assertIn("public_hosting", c)
        self.assertEqual(connector("claude").login()["kind"], "command")

    def test_claude_connector_runs_the_pinned_mcp_server_and_returns_its_log(self):
        run, events = self.ask("claude")
        self.assertEqual([e["name"] for e in events], ["benchmark_usage"])
        self.assertEqual(run.provider_session, "sess-stub-1")
        # the trail is the MCP server's own record: name, arguments, result, revision
        self.assertEqual([(t["name"], t["arguments"], t["revision"]) for t in run.trail], [("benchmark_usage", {"topic": "robotics"}, "v1")])
        self.assertEqual(run.trail[0]["result"]["results"][0]["papers"], 1)
        self.assertIn("⟦benchmark_usage", run.text)
        self.assertEqual(run.trail_for_turn(), [{"name": "benchmark_usage", "arg": '{"topic": "robotics"}'}])

    def test_codex_connector_same_contract(self):
        run, events = self.ask("codex")
        self.assertEqual([e["name"] for e in events], ["benchmark_scope"])
        self.assertEqual(run.provider_session, "thread-stub-1")
        self.assertEqual([(t["name"], t["revision"]) for t in run.trail], [("benchmark_scope", "v1")])
        self.assertEqual(run.trail[0]["result"]["revision"], "v1")
        self.assertEqual(run.text, "Scope checked.")

    def test_per_account_config_dir_reaches_the_cli_environment(self):
        seen = {}
        original = cli_agents.subprocess.Popen

        def spy(cmd, **kw):
            seen.update(kw["env"])
            return original(cmd, **kw)
        with patch.object(cli_agents.subprocess, "Popen", side_effect=spy):
            self.ask("claude", config_dir=str(self.root / "acct"))
        self.assertEqual(seen["CLAUDE_CONFIG_DIR"], str(self.root / "acct"))

    def test_cancel_terminates_the_cli_and_yields_no_answer(self):
        flag = threading.Event()
        threading.Timer(0.3, flag.set).start()
        with patch.dict(os.environ, {"STUB_SLEEP": "5"}):
            with self.assertRaisesRegex(ConnectorError, "cancelled"):
                self.ask("claude", cancelled=flag.is_set)
        self.assertFalse(self.log.exists())                                # the tool was never reached

    def test_missing_revision_is_an_error_not_other_data(self):
        with self.assertRaises(Exception):
            connector("claude").ask(root=self.root, revision="v9", question="q", system="s", history=None,
                                    provider_session=None, emit=lambda e: None, cancelled=lambda: False, tool_log=self.log)

    def test_api_key_connector_records_calls_in_the_same_shape(self):
        script = [
            {"status": "completed", "output": [{"type": "function_call", "call_id": "c1", "name": "benchmark_usage", "arguments": "{}"}]},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "done ⟦benchmark_usage:{}|1 paper⟧"}]}]},
        ]
        with patch.object(providers, "post", side_effect=lambda *a: script.pop(0)), providers.using_credentials({"openai": "k"}):
            run, events = self.ask("api", provider="openai")
        self.assertEqual([(t["name"], t["revision"], t["result"]["results"][0]["papers"]) for t in run.trail], [("benchmark_usage", "v1", 1)])
        self.assertEqual(run.provider_session, providers.DEFAULT_MODELS["openai"])
        self.assertIsNotNone(run.messages)
        self.assertFalse(connector("api", provider="openai").status()["connected"])


if __name__ == "__main__":
    unittest.main()
