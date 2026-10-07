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
    env.pop("BENCHTREND_HOME", None)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        snapshot = root / "data/processed/benchmark_snapshot.json"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(json.dumps({
            "schema_version": 1, "snapshot_id": "installed-wheel-fixture", "source": "arxiv_html_stated_roles",
            "generated_at": "2026-01-01", "benchmarks": [{"id": "toy", "name": "Toy"}],
            "editions": {"icml-2025": {"total_papers": 1, "arxiv_matched": 1, "parsed_papers": 1}},
            "papers": [{"paper_id": "2501.00001", "title": "Synthetic policy", "edition": "icml-2025",
                        "fields": ["robotics"], "uses": {"toy": {"roles": {"evaluates_on": [{
                            "where": "experiments", "text": "We evaluate our policies on the Toy benchmark."}]}}}}]
        }), encoding="utf-8")

        def run(args, input=None):
            result = subprocess.run([sys.executable, "-m", "benchtrend", "--home", str(root), *args],
                                    cwd=root, env=env, input=input, capture_output=True, text=True, timeout=20)
            if result.returncode:
                raise AssertionError(result.stderr)
            return result.stdout

        assert "BenchTrend 0.2.0" in run(["--version"])
        assert json.loads(run(["status", "--json"]))["data"]["available"]
        config = json.loads(run(["mcp", "--config"]))["mcpServers"]["benchtrend"]
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
        assert len(response[1]["result"]["tools"]) == 6
        usage = json.loads(response[2]["result"]["content"][0]["text"])
        assert usage["results"][0]["papers"] == 1
        assert usage["coverage"]["parsed_papers"] == 1
        print("Installed wheel: version, external data, MCP configuration and benchmark tools passed.")


if __name__ == "__main__":
    main()
