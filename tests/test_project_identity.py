"""Public launchers stay usable after consolidating terminal and server code."""
import os
import unittest
from unittest.mock import patch

from bench2agent import cli
from bench2agent.core import cli as service
from bench2agent.core.paths import CODE_ROOT, agent_environment, runtime_root


class ProjectIdentityTests(unittest.TestCase):
    def test_checkout_root_and_mcp_environment_survive_package_nesting(self):
        self.assertTrue((CODE_ROOT / "pyproject.toml").is_file())
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(runtime_root(), CODE_ROOT)
            self.assertEqual(agent_environment()["PYTHONPATH"], str(CODE_ROOT / "src"))

    def test_public_command_routes_web_and_inventory_operations(self):
        cases = ((["serve", "--port", "8123", "--no-meili"], "cmd_serve"),
                 (["datasets", "--out", "/tmp/bench2agent-inventory", "--gid", "7"], "cmd_datasets"),
                 (["benchmarks", "--out", "snapshot.json"], "cmd_benchmarks"))
        for argv, handler in cases:
            with self.subTest(command=argv[0]), patch.object(service, handler, return_value=0) as fn:
                self.assertEqual(cli.main(argv), 0)
                args = fn.call_args.args[0]
                self.assertEqual(args.command, argv[0])
                if argv[0] == "serve":
                    self.assertEqual(args.port, 8123)
                    self.assertTrue(args.no_meili)
                elif argv[0] == "datasets":
                    self.assertEqual(args.gid, 7)

    def test_browser_mcp_retains_paper_tools(self):
        from bench2agent.core.chat import _mcp_config
        import json
        server = json.loads(_mcp_config())["mcpServers"]["bench2agent"]
        self.assertEqual(server["args"], ["-m", "bench2agent.core", "mcp"])
