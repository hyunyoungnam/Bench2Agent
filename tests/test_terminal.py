"""Terminal/API/MCP behavior against synthetic evidence and real local HTTP."""
import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from benchtrend import cli, data, providers
from benchtrend.session import Session
from bellwether import mcp
from bellwether.benchmarks import BenchmarkStore


QUOTE = "We evaluate our policies on the Toy benchmark."


def snapshot():
    return {"schema_version": 1, "snapshot_id": "fixture-1", "generated_at": "2026-01-01",
            "source": "arxiv_html_stated_roles", "benchmarks": [{"id": "toy", "name": "Toy"}],
            "editions": {"icml-2025": {"total_papers": 2, "arxiv_matched": 2, "parsed_papers": 2}},
            "papers": [{"paper_id": "2501.00001", "edition": "icml-2025", "title": "A robot policy",
                        "fields": ["robotics"], "authors": ["Researcher One"],
                        "uses": {"toy": {"roles": {"evaluates_on": [{"text": QUOTE, "where": "experiments"}]}}}},
                       {"paper_id": "2501.00002", "edition": "icml-2025", "title": "Another policy",
                        "fields": ["robotics"], "authors": ["Researcher Two"], "uses": {}}]}


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / data.MEMBER
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(snapshot()), encoding="utf-8")
        self.store = BenchmarkStore(self.root)
        self.patch = patch.object(mcp, "B", self.store)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_bundle_installs_without_browser_or_pipeline(self):
        bundle = self.root / "release.tar.gz"
        result = data.bundle(self.root, bundle)
        target = self.root / "fresh"
        installed = data.install(target, file=str(bundle), sha256=result["sha256"])
        self.assertEqual(installed["snapshot_id"], "fixture-1")
        self.assertEqual(BenchmarkStore(target).usage({})["results"][0]["papers"], 1)
        self.assertFalse((target / "reports").exists())
        self.assertFalse((target / "data/interim").exists())

    def test_invalid_data_and_bad_checksums_preserve_existing_snapshot(self):
        original = self.path.read_bytes()
        wrong = self.root / "wrong.json"
        wrong.write_text('{}')
        with self.assertRaises(ValueError):
            data.install(self.root, file=str(wrong))
        with self.assertRaises(ValueError):
            data.install(self.root, file=str(self.path), sha256="0" * 64)
        self.assertEqual(self.path.read_bytes(), original)

    def test_bundle_links_do_not_escape_install_root(self):
        bundle = self.root / "unsafe.tar.gz"
        with tarfile.open(bundle, "w:gz") as archive:
            member = tarfile.TarInfo(data.MEMBER)
            member.type = tarfile.SYMTYPE
            member.linkname = "../../../outside"
            archive.addfile(member)
        with self.assertRaises(ValueError):
            data.install(self.root, file=str(bundle))
        self.assertEqual(data.status(self.root)["snapshot_id"], "fixture-1")

    def test_tools_validate_arguments_and_keep_data_missing_distinct(self):
        tools = mcp._benchmark_tools()
        self.assertIn("error", mcp.execute_tool("benchmark_usage", {"latest": "false"}, tools))
        self.assertIn("error", mcp.execute_tool("benchmark_usage", {"years": [True]}, tools))
        self.assertIn("error", mcp.execute_tool("benchmark_usage", {"role": "mentions"}, tools))
        self.assertIn("error", mcp.execute_tool("benchmark_trend", {}, tools))
        self.assertIn("error", mcp.execute_tool("benchmark_usage", {"invented": 1}, tools))
        self.assertIn("error", mcp.execute_tool("benchmark_usage", None, tools))
        self.path.unlink()
        self.assertFalse(mcp.execute_tool("benchmark_scope", {}, tools)["available"])
        self.assertIn("error", mcp.execute_tool("benchmark_usage", {}, tools))

    def test_mcp_is_a_clean_read_only_protocol_and_reuses_tools(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "benchmark_usage", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "shell", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "ping"}]
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO("\n".join(map(json.dumps, messages)))), patch("sys.stdout", output):
            mcp.serve_stdio(mcp._benchmark_tools(), name="benchtrend")
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in responses], [1, 2, 3, 4, 5])
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "benchtrend")
        self.assertNotEqual(responses[0]["result"]["protocolVersion"], "2099-01-01")
        self.assertIn("denominator", responses[0]["result"]["instructions"])
        tools = responses[1]["result"]["tools"]
        self.assertEqual(len(tools), 6)
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in tools))
        response = json.loads(responses[2]["result"]["content"][0]["text"])
        self.assertEqual(response, self.store.usage({}))
        self.assertIn("error", responses[3])

    def test_mcp_parse_errors_do_not_kill_server(self):
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO('bad json\n[]\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n')), patch("sys.stdout", output):
            mcp.serve_stdio(mcp._benchmark_tools())
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r.get("error", {}).get("code") for r in responses], [-32700, -32600, None])

    def test_native_registration_uses_absolute_executable_and_preserves_other_entries(self):
        args = type("Args", (), {"config": False, "connect": "claude", "dry_run": False})()
        with patch("shutil.which", return_value="/client/claude"), patch("subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()):
            run.return_value.returncode = 0
            self.assertEqual(cli.cmd_mcp(args, self.root), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["/client/claude", "mcp", "add"])
        self.assertEqual(command[command.index("--transport"):command.index("--transport") + 5],
                         ["--transport", "stdio", "--scope", "user", "benchtrend"])
        self.assertIn("user", command)
        index = command.index("--")
        self.assertEqual(command[index + 1], sys.executable)
        self.assertIn(str(self.root), command[index + 1:])
        self.assertNotIn("remove", command)

    def test_dry_run_does_not_change_client_configuration(self):
        args = type("Args", (), {"config": False, "connect": "codex", "dry_run": True})()
        with patch("shutil.which", return_value="/client/codex"), patch("subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()) as output:
            cli.cmd_mcp(args, self.root)
        run.assert_not_called()
        self.assertIn("mcp add benchtrend", output.getvalue())

    def test_default_data_install_cli_verifies_the_release_checksum(self):
        bundle = self.root / "release.tar.gz"
        release = data.bundle(self.root, bundle)
        original = self.path.read_bytes()
        self.path.unlink()

        def download(url, dest):
            self.assertEqual(url, data.DEFAULT_URL)
            shutil.copyfile(bundle, dest)

        with patch("bellwether.paths.ROOT", self.root), \
             patch("bellwether.cli._download", side_effect=download) as fetch, \
             patch.object(data, "DEFAULT_SHA256", release["sha256"]), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["data", "install"]), 0)
        fetch.assert_called_once()
        self.assertEqual(self.path.read_bytes(), original)
        with patch("bellwether.cli._download", side_effect=download), \
             patch.object(data, "DEFAULT_SHA256", "0" * 64):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                data.install(self.root)
        self.assertEqual(self.path.read_bytes(), original)

    def test_first_launch_downloads_once_and_never_replaces_installed_data(self):
        bundle = self.root / "release.tar.gz"
        release = data.bundle(self.root, bundle)
        self.path.unlink()
        with patch("bellwether.cli._download", side_effect=lambda url, dest: shutil.copyfile(bundle, dest)) as fetch, \
             patch.object(data, "DEFAULT_SHA256", release["sha256"]), \
             patch("builtins.input", side_effect=AssertionError("no URL prompt")), \
             contextlib.redirect_stderr(io.StringIO()):
            cli.require_data(self.root, interactive=True)
            cli.require_data(self.root, interactive=True)
        fetch.assert_called_once()
        self.assertEqual(data.status(self.root)["snapshot_id"], "fixture-1")

    def test_headless_query_does_not_download_data_implicitly(self):
        self.path.unlink()
        with patch.object(data, "install") as install:
            with self.assertRaisesRegex(ValueError, "benchtrend data install"):
                cli.require_data(self.root, interactive=False)
        install.assert_not_called()

    def test_shortcuts_launch_repeatedly_with_data_bound_to_the_session(self):
        for name in ("claude", "codex"):
            with self.subTest(client=name), patch("shutil.which", return_value="/client/" + name), \
                 patch("subprocess.run") as run, patch.object(data, "install") as install, \
                 contextlib.redirect_stderr(io.StringIO()):
                args = type("Args", (), {"command": name, "dry_run": False})()
                run.return_value.returncode = 0
                self.assertEqual(cli.cmd_client(args, self.root), 0)
                self.assertEqual(cli.cmd_client(args, self.root), 0)
                self.assertEqual(run.call_count, 2)
                install.assert_not_called()
                command = run.call_args.args[0]
                self.assertEqual(command[0], "/client/" + name)
                self.assertNotIn("add", command)
                self.assertNotIn("remove", command)
                if name == "claude":
                    server = json.loads(command[2])["mcpServers"]["benchtrend"]
                    self.assertEqual(server["command"], sys.executable)
                    self.assertEqual(server["env"]["BENCHTREND_HOME"], str(self.root))
                    self.assertIn(str(self.root), server["args"])
                    self.assertEqual(command[1], "--mcp-config")
                    self.assertNotIn("--strict-mcp-config", command)
                else:
                    settings = dict(value.split("=", 1) for flag, value in zip(command[1::2], command[2::2]) if flag == "-c")
                    self.assertEqual(json.loads(settings["mcp_servers.benchtrend.command"]), sys.executable)
                    self.assertIn(str(self.root), json.loads(settings["mcp_servers.benchtrend.args"]))
                    self.assertIn(json.dumps(str(self.root)), settings["mcp_servers.benchtrend.env"])
                    self.assertEqual(command[-2:], ["--model", "gpt-6.1-sol"])
                run.return_value.returncode = 7
                self.assertEqual(cli.cmd_client(args, self.root), 7)

    def test_shortcut_missing_client_or_bad_download_never_starts_a_session(self):
        args = type("Args", (), {"command": "claude", "dry_run": False})()
        self.path.unlink()
        with patch("shutil.which", return_value=None), patch.object(data, "install") as install:
            with self.assertRaisesRegex(ValueError, "Install it first"):
                cli.cmd_client(args, self.root)
        install.assert_not_called()
        with patch("shutil.which", return_value="/client/claude"), patch("subprocess.run") as run, \
             patch.object(data, "install", side_effect=ValueError("SHA-256 mismatch")), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                cli.cmd_client(args, self.root)
        run.assert_not_called()

    def test_shortcut_dry_run_has_no_download_or_client_side_effects(self):
        self.path.unlink()
        args = type("Args", (), {"command": "claude", "dry_run": True})()
        with patch("shutil.which", return_value="/client/claude"), patch.object(data, "install") as install, \
             patch("subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.cmd_client(args, self.root), 0)
        install.assert_not_called()
        run.assert_not_called()
        self.assertIn("--mcp-config", output.getvalue())

    def test_shortcut_cli_dispatch(self):
        for name in ("claude", "codex"):
            with self.subTest(client=name), patch.object(cli, "cmd_client", return_value=0) as launch:
                self.assertEqual(cli.main([name]), 0)
                self.assertEqual(launch.call_args.args[0].command, name)

    def test_default_openai_model_reaches_every_responses_request(self):
        args = type("Args", (), {"provider": "openai", "model": None, "language": None})()
        config = cli.configuration(args, self.root)
        session = Session(self.root, **config)
        with patch.object(providers, "post", side_effect=self.scripted("openai")) as post:
            session.ask("Which benchmarks are used?", lambda event: None)
        self.assertTrue(post.call_args_list)
        for call in post.call_args_list:
            self.assertEqual(call.args[0:2], ("openai", "/responses"))
            self.assertEqual(call.args[2]["model"], "gpt-6.1-sol")

    def test_explicit_and_saved_models_remain_available(self):
        args = type("Args", (), {"provider": "openai", "model": None, "language": None})()
        cli.write_json(cli.settings_path(self.root), {"provider": "openai", "model": "custom-model", "language": "en"})
        self.assertEqual(cli.configuration(args, self.root)["model"], "custom-model")
        args.model = "gpt-5-mini"
        self.assertEqual(cli.configuration(args, self.root)["model"], "gpt-5-mini")
        with patch("shutil.which", return_value="/client/codex"):
            self.assertEqual(cli.client_command("codex", self.root, model="custom-model")[-2:], ["--model", "custom-model"])
        with patch("shutil.which", return_value="/client/claude"):
            self.assertEqual(cli.client_command("claude", self.root, model="custom-model")[-2:], ["--model", "custom-model"])

    def test_verified_render_shows_sources_and_marks_fabrications(self):
        from bellwether.chat import segment
        segs, status = segment("Toy ⟦benchmark_usage:{}|1 paper⟧ ⟦arxiv:2501.00001|" + QUOTE +
                               "⟧ ⟦arxiv:2501.00001|This sentence was never written in the paper.⟧", None, None)
        text = cli.render({"segs": segs, "verified": status}, sources=True)
        self.assertIn("✓ A robot policy", text)
        self.assertIn("[unverified quote]", text)
        self.assertIn(QUOTE, text)
        self.assertIn("https://arxiv.org/abs/2501.00001", text)
        self.assertIn("Quotes 1/2", text)
        self.assertNotIn("\x1b", cli.safe_text("\x1b[2Jtitle\x00"))
        from bellwether.figures import claimed
        self.assertEqual(claimed("500 per 1,000 parsed papers, 1000 uses"), [("500", 500), ("1000", 1000)])
        self.assertEqual(claimed("논문 1,000편당 500편, 전체 1000편"), [("500", 500), ("1000", 1000)])

    def test_default_interactive_conversation_sources_and_new(self):
        from benchtrend.session import conversations
        args = type("Args", (), {"provider": "openai", "model": "fixture-model", "language": "ko",
                                 "resume": None, "json": False})()
        lines = ["로보틱스에서 무엇을 쓰나요?", "/sources", "/new", "새로운 질문", "/exit"]
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=lines), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-secret"}), \
             patch.object(providers, "post", side_effect=self.scripted("openai") * 2), \
             contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_chat(args, self.root), 0)
        self.assertIn(QUOTE, output.getvalue())
        self.assertIn("New conversation", output.getvalue())
        self.assertIn("Continue: benchtrend --resume", output.getvalue())
        self.assertEqual(len(conversations(self.root)), 2)
        self.assertNotIn("fixture-secret", cli.settings_path(self.root).read_text())

    def test_bare_command_dispatches_to_conversation(self):
        with patch.object(cli, "cmd_chat", return_value=0) as chat:
            self.assertEqual(cli.main([]), 0)
        chat.assert_called_once()

    def test_legacy_quote_without_gid_index_stays_unverified(self):
        from bellwether.chat import segment
        from bellwether.verify import Verifier
        store = mcp.Store()
        with patch.object(store, "rec", side_effect=FileNotFoundError):
            segments, status = segment("⟦9|This invented quote has no paper in the installed data.⟧", store, Verifier(store))
        self.assertFalse(segments[0]["v"])
        self.assertEqual(status["passed"], 0)

    def scripted(self, provider):
        final = 'Toy: ⟦benchmark_usage:{"topic":"robotics"}|1 paper, 500 per 1,000 parsed papers⟧. '
        final += "⟦arxiv:2501.00001|" + QUOTE + "⟧"
        def openai(call_id, name, args):
            return {"status": "completed", "output": [{"type": "reasoning", "id": "r" + call_id,
                    "encrypted_content": "encrypted-fixture", "summary": []},
                   {"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(args)}]}
        def anthropic(call_id, name, args):
            return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": call_id, "name": name, "input": args}]}
        factory = openai if provider == "openai" else anthropic
        rounds = [factory("scope", "benchmark_scope", {}), factory("use", "benchmark_usage", {"topic": "robotics"})]
        if provider == "openai":
            rounds.append({"status": "completed", "output": [{"type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": final}]}]})
        else:
            rounds.append({"stop_reason": "end_turn", "content": [{"type": "text", "text": final}]})
        return rounds

    def test_both_api_protocols_preserve_history_and_verify_answers(self):
        for provider in ("openai", "anthropic"):
            with self.subTest(provider=provider):
                requests = []
                script = self.scripted(provider) * 2
                def post(_, path, payload):
                    requests.append(copy.deepcopy(payload))
                    return script[len(requests) - 1]
                session = Session(self.root, provider, "fixture-model")
                with patch.object(providers, "post", side_effect=post):
                    first = session.ask("What do people use in robotics?", lambda _: None)
                    resumed = Session(self.root, provider, "ignored-model", cid=session.doc["id"])
                    second = resumed.ask("And in the same field?", lambda _: None)
                self.assertEqual(first["verified"]["passed"], 1)
                self.assertEqual(first["verified"]["fchecked"], first["verified"]["fpassed"])
                self.assertEqual(second["on"]["benchmarks"]["snapshot_id"], "fixture-1")
                self.assertEqual(len(resumed.doc["turns"]), 2)
                self.assertEqual(resumed.doc["model"], "fixture-model")
                history = requests[3]["input" if provider == "openai" else "messages"]
                self.assertIn("What do people use in robotics?", json.dumps(history))
                if provider == "openai":
                    self.assertFalse(requests[0]["store"])
                    self.assertIn("encrypted-fixture", json.dumps(requests[2]["input"]))
                else:
                    messages = requests[1]["messages"]
                    self.assertEqual(messages[-2]["content"][0]["type"], "tool_use")
                    self.assertEqual(messages[-1]["content"][0]["tool_use_id"], "scope")

    def test_failed_turn_is_not_saved_or_added_to_model_history(self):
        session = Session(self.root, "openai", "fixture-model")
        with patch.object(providers, "post", side_effect=providers.ProviderError("quota")):
            with self.assertRaises(providers.ProviderError):
                session.ask("A question", lambda _: None)
        self.assertFalse(session.path.exists())
        self.assertEqual(session.doc["messages"], [])

    def test_data_update_and_concurrent_resume_do_not_mix_conversations(self):
        session = Session(self.root, "openai", "fixture-model")
        with patch.object(providers, "post", side_effect=self.scripted("openai")):
            session.ask("A question", lambda _: None)
        earlier = Session(self.root, "openai", "fixture-model", cid=session.doc["id"])
        with patch.object(providers, "post", side_effect=self.scripted("openai")):
            session.ask("Another question", lambda _: None)
        with self.assertRaisesRegex(ValueError, "another terminal"):
            earlier.ask("Stale question", lambda _: None)
        changed = snapshot()
        changed["snapshot_id"] = "fixture-2"
        self.path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "dataset changed"):
            session.ask("A follow-up", lambda _: None)

    def test_api_errors_do_not_echo_secrets(self):
        import urllib.error
        error = urllib.error.HTTPError("https://endpoint", 401, "SECRET_KEY", {}, io.BytesIO(b"SECRET_KEY"))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "SECRET_KEY"}), patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(providers.ProviderError, "API key rejected") as caught:
                providers.post("openai", "/responses", {})
        self.assertNotIn("SECRET_KEY", str(caught.exception))

    def test_cli_round_trip_against_real_local_http_for_both_providers(self):
        for provider in ("openai", "anthropic"):
            with self.subTest(provider=provider):
                script = self.scripted(provider) * 2
                requests = []
                class Handler(BaseHTTPRequestHandler):
                    def do_POST(self):
                        requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))), dict(self.headers)))
                        reply = json.dumps(script[len(requests) - 1]).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(reply)))
                        self.end_headers()
                        self.wfile.write(reply)
                    def log_message(self, *args):
                        pass
                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                       providers.KEY_NAMES[provider]: "fixture-secret",
                       "BENCHTREND_" + provider.upper() + "_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1"}
                command = [sys.executable, "-m", "benchtrend", "--home", str(self.root), "--provider", provider,
                           "ask", "What do researchers use?", "--json"]
                try:
                    first = subprocess.run(command, cwd=self.tmp.name, env=env, capture_output=True, text=True, timeout=20)
                    self.assertEqual(first.returncode, 0, first.stderr)
                    turn = json.loads(first.stdout)
                    self.assertEqual(turn["verified"]["passed"], 1)
                    second = subprocess.run(command + ["--resume", turn["chat"]], cwd=self.tmp.name,
                                            env=env, capture_output=True, text=True, timeout=20)
                    self.assertEqual(second.returncode, 0, second.stderr)
                    self.assertEqual(json.loads(second.stdout)["chat"], turn["chat"])
                    self.assertNotIn("fixture-secret", first.stdout + first.stderr + second.stdout + second.stderr)
                    history = (self.root / "data/benchtrend/chats" / (turn["chat"] + ".json")).read_text()
                    self.assertNotIn("fixture-secret", history)
                    self.assertEqual(len(requests), 6)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()


class ClientStatusTests(unittest.TestCase):
    """status reports MCP registration from the clients' CLIs, never their files."""

    def test_registered_and_connected_is_read_from_the_cli(self):
        from unittest import mock
        from benchtrend import cli as terminal

        class Done:
            returncode = 0
            stdout = "benchtrend:\n  Scope: User config (available in all your projects)\n  Status: ✔ Connected\n"

        agent = {"installed": True, "authenticated": True}
        with mock.patch("bellwether.chat._agent_status", return_value=agent):
            clients = terminal.client_status(run=lambda *a, **k: Done())
        self.assertEqual(clients["claude"], {"installed": True, "signed_in": True, "mcp_registered": True,
                                             "mcp_scope": "user", "mcp_connected": True})
        self.assertIn("registered (user scope)", terminal._client_line("claude", clients["claude"]))
        self.assertIn("connected", terminal._client_line("claude", clients["claude"]))

    def test_missing_registration_names_the_connect_command(self):
        from unittest import mock
        from benchtrend import cli as terminal

        class Missing:
            returncode = 1
            stdout = "Error: No MCP server named 'benchtrend' found."

        agent = {"installed": True, "authenticated": None}
        with mock.patch("bellwether.chat._agent_status", return_value=agent):
            line = terminal._client_line("codex", terminal.client_status(run=lambda *a, **k: Missing())["codex"])
        self.assertIn("not registered", line)
        self.assertIn("benchtrend mcp --connect codex", line)
        self.assertIn("sign-in unknown", line)

    def test_not_installed(self):
        from unittest import mock
        from benchtrend import cli as terminal
        with mock.patch("bellwether.chat._agent_status", return_value={"installed": False, "authenticated": None}):
            clients = terminal.client_status(run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
        self.assertEqual(terminal._client_line("claude", clients["claude"]), "Claude Code: not installed")
