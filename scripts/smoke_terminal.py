"""Verify an installed wheel outside its checkout, with synthetic data only.

Run with the fresh environment's Python: python scripts/smoke_terminal.py.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("BENCH2AGENT_HOME", None)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        snapshot = root / "fixture.json"
        snapshot.write_text(json.dumps({
            "schema_version": 1, "snapshot_id": "installed-wheel-fixture", "source": "arxiv_html_stated_roles",
            "generated_at": "2026-01-01", "benchmarks": [{"id": "toy", "name": "Toy"}],
            "editions": {"icml-2025": {"total_papers": 1, "arxiv_matched": 1, "parsed_papers": 1}},
            "papers": [{"paper_id": "2501.00001", "title": "Synthetic policy", "edition": "icml-2025",
                        "fields": ["robotics"], "uses": {"toy": {"roles": {"evaluates_on": [{
                            "where": "experiments", "text": "We evaluate our policies on the Toy benchmark."}]}}}}]
        }), encoding="utf-8")

        def run(args, input=None):
            result = subprocess.run([sys.executable, "-m", "bench2agent", "--home", str(root), *args],
                                    cwd=root, env=env, input=input, capture_output=True, text=True, timeout=20)
            if result.returncode:
                raise AssertionError(result.stderr)
            return result.stdout

        from bench2agent import __version__
        assert "Bench2Agent " + __version__ in run(["--version"])
        installed = json.loads(run(["data", "install", "--file", str(snapshot)]))
        assert installed["revision"] == "installed-wheel-fixture" and installed["outcome"] == "installed"
        executable = Path(sys.executable).parent / ("bench2agent.exe" if os.name == "nt" else "bench2agent")
        assert executable.is_file(), "The installed bench2agent console command is missing"
        assert subprocess.run([str(executable), "--version"], cwd=root, env=env,
                              capture_output=True, text=True, check=True).stdout.strip() == "Bench2Agent " + __version__
        assert "--no-meili" in run(["serve", "--help"])
        assert json.loads(run(["status", "--json"]))["data"]["available"]
        config = json.loads(run(["mcp", "--config"]))["mcpServers"]["bench2agent"]
        assert config["command"] == sys.executable
        assert str(root) in config["args"]
        assert "PYTHONPATH" not in config["env"]  # installed code, no source checkout
        request = "\n".join(json.dumps(message) for message in [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "benchmark_usage", "arguments": {}}}
        ]) + "\n"
        response = [json.loads(line) for line in run(["mcp"], request).splitlines()]
        assert len(response) == 3
        assert response[0]["result"]["serverInfo"]["name"] == "bench2agent"
        assert len(response[1]["result"]["tools"]) == 6
        usage = json.loads(response[2]["result"]["content"][0]["text"])
        assert usage["results"][0]["papers"] == 1
        assert usage["coverage"]["parsed_papers"] == 1
        # the revision is a database; a legacy JSON installs through the one-time
        # conversion, which the utility also offers (never into a revision)
        status = json.loads(run(["data", "status"]))
        assert status["format"] == "bench2agent-data-v2" and status["database"]["available"]
        assert status["files"] == ["benchmark_snapshot.json", "benchmark_snapshot.sqlite"]
        built = json.loads(run(["data", "sqlite", str(snapshot), "--out", str(root / "built.sqlite")]))
        assert built["paper_observations"] == 1 and built["db_schema_version"] == 1
        bundled = json.loads(run(["data", "bundle", "--out", str(root / "bundle.tar.gz")]))
        assert bundled["format"] == "bench2agent-data-v2"
        reinstalled = json.loads(run(["--home", str(root / "second"), "data", "install", "--file", str(root / "bundle.tar.gz")]))
        assert reinstalled["revision"] == "installed-wheel-fixture" and reinstalled["files"] == ["benchmark_snapshot.sqlite"]
        assert json.loads(run(["--home", str(root / "second"), "data", "status"]))["available"]
        previous = root / "previous/data/benchtrend/chats"
        previous.mkdir(parents=True)
        (previous / "012345abcdef.json").write_text(json.dumps({
            "id": "012345abcdef", "title": "Preserved conversation", "provider": "openai",
            "model": "saved-model", "ts": 1}), encoding="utf-8")
        migrated = json.loads(run(["migrate", "--from", str(root / "previous")]))
        assert migrated["chats_copied"] == 1
        assert "Preserved conversation" in run(["chats"])
        # Exercise the new CLI-login runtime against the installed package and
        # its real stdio MCP subprocess, without a provider key or model call.
        if os.name != "nt":
            from types import SimpleNamespace
            from unittest.mock import patch
            from bench2agent import cli
            binary = root / "bin/claude"
            binary.parent.mkdir()
            binary.write_text("#!" + sys.executable + "\n" + '''
import json, os, subprocess, sys
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "stub"})); sys.exit(0)
cfg = json.loads(args[args.index("--mcp-config") + 1])["mcpServers"]["bench2agent"]
env = dict(os.environ); env.update(cfg["env"])
req = "\\n".join(json.dumps(m) for m in [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "benchmark_usage", "arguments": {}}}]) + "\\n"
out = subprocess.run([cfg["command"], *cfg["args"]], input=req, capture_output=True, text=True, env=env, check=True)
result = json.loads(json.loads(out.stdout.splitlines()[-1])["result"]["content"][0]["text"])
assert result["revision"] == "installed-wheel-fixture"
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__bench2agent__benchmark_usage", "input": {}}]}}))
print(json.dumps({"type": "result", "session_id": "fixture-session", "result": 'Toy ⟦benchmark_usage:{}|1 paper⟧ ⟦arxiv:2501.00001|We evaluate our policies on the Toy benchmark.⟧'}))
''', encoding="utf-8")
            binary.chmod(0o700)
            args = SimpleNamespace(provider=None, model=None, language=None, connector="claude", resume=None)
            with patch("builtins.input") as prompt:
                session = cli.load_session(args, root, interactive=True)
            prompt.assert_not_called()
            assert cli.saved_connector(root) == "claude"
            agent_env = {**env, "PATH": str(binary.parent) + os.pathsep + env["PATH"]}
            agent_env.pop("OPENAI_API_KEY", None)
            agent_env.pop("ANTHROPIC_API_KEY", None)
            result = subprocess.run([str(executable), "--home", str(root), "ask", "Which benchmarks?", "--json"],
                                    cwd=root, env=agent_env, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            turn = json.loads(result.stdout)
            assert turn["connector"] == "claude" and not turn["unverifiable"]
            assert turn["verified"]["passed"] == turn["verified"]["checked"] == 1
            assert turn["verified"]["fpassed"] == turn["verified"]["fchecked"] == 1
        print("Installed wheel: version, external data, MCP, migration and CLI-login runtime passed.")


if __name__ == "__main__":
    main()
