"""The six tools over Streamable HTTP: revision by path, outputs identical to
the repository reference, overlapping conversations isolated. Runs only where
the server extra is installed (pip install 'bench2agent[server]')."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

try:
    import httpx
    import mcp  # noqa: F401
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
except ImportError:  # pragma: no cover - the standard-library runtime
    httpx = None

from bench2agent import data as data_module
from bench2agent.core import revisions
from bench2agent.core.benchmarks import BenchmarkStore
try:
    from test_revisions import QUOTE_V1, QUOTE_V2, snapshot
except ImportError:                                  # run as tests.test_http_mcp
    from tests.test_revisions import QUOTE_V1, QUOTE_V2, snapshot


@unittest.skipUnless(httpx, "server extra not installed")
class HttpMcpTests(unittest.TestCase):
    def setUp(self):
        from bench2agent.core.mcp_http import HttpMcp
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "v1.json").write_text(json.dumps(snapshot("v1", QUOTE_V1)))
        (self.root / "v2.json").write_text(json.dumps(snapshot("v2", QUOTE_V2)))
        data_module.install(self.root, file=str(self.root / "v1.json"))
        self.app = HttpMcp(self.root)                 # pinned to v1, the active revision at start

    def run_app(self, coro):
        """Run `coro(client)` against the ASGI app with its lifespan started."""
        async def main():
            async with self.lifespan():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1:8765") as client:
                    return await coro(client)
        return asyncio.run(main())

    def lifespan(self):
        app = self.app

        class _Lifespan:
            async def __aenter__(self):
                import anyio
                self.send_q = anyio.create_memory_object_stream(10)
                self.recv_q = anyio.create_memory_object_stream(10)
                self.task = asyncio.ensure_future(app({"type": "lifespan"}, self.recv_q[1].receive, self.send_q[0].send))
                await self.recv_q[0].send({"type": "lifespan.startup"})
                started = await self.send_q[1].receive()
                assert started["type"] == "lifespan.startup.complete", started
                return self

            async def __aexit__(self, *exc):
                await self.recv_q[0].send({"type": "lifespan.shutdown"})
                await self.send_q[1].receive()
                await self.task
                for stream in (*self.send_q, *self.recv_q):
                    await stream.aclose()
        return _Lifespan()

    @staticmethod
    async def call(client, path, name, arguments):
        async with streamable_http_client(f"http://127.0.0.1:8765{path}", http_client=client) as (read, write, *_):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
        return json.loads(result.content[0].text), result.is_error

    def test_outputs_match_the_repository_reference_on_the_same_revision(self):
        reference = BenchmarkStore(self.root, revision="v1")
        calls = [("benchmark_scope", {}), ("benchmark_usage", {"topic": "robotics"}), ("benchmark_trend", {"benchmark": "toy"}),
                 ("benchmark_evidence", {"benchmark": "toy"}), ("new_benchmarks", {}), ("benchmark_usage", {"role": "cites"})]

        async def scenario(client):
            out = []
            for name, args in calls:
                out.append(await self.call(client, "/revisions/v1/mcp", name, args))
                out.append(await self.call(client, "/mcp", name, args))
            return out
        results = self.run_app(scenario)
        for (name, args), (via_path, err1), (via_pinned, err2) in zip(calls, results[0::2], results[1::2]):
            with self.subTest(tool=name, args=args):
                method = {"benchmark_scope": "scope", "benchmark_usage": "usage", "benchmark_trend": "trend",
                          "benchmark_evidence": "evidence", "new_benchmarks": "new"}[name]
                try:
                    expected = getattr(reference, method)(dict(args))
                except ValueError as exc:
                    expected = {"error": str(exc)}
                if "error" in expected:
                    # argument errors are caught by the shared schema validation on
                    # the transport path, by the store on the direct path: both refuse
                    self.assertIn("error", via_path)
                else:
                    self.assertEqual(json.dumps(via_path, sort_keys=True), json.dumps(expected, sort_keys=True))
                self.assertEqual(via_path, via_pinned)
                self.assertEqual(err1, "error" in expected)

    def test_path_decides_the_revision_and_a_publish_does_not_move_pinned_clients(self):
        async def scenario(client):
            before = await self.call(client, "/mcp", "benchmark_usage", {})
            missing = await client.post("/revisions/v2/mcp", json={})
            data_module.install(self.root, file=str(self.root / "v2.json"))     # v2 goes live while the server runs
            listing = (await client.get("/revisions")).json()
            pinned = await self.call(client, "/mcp", "benchmark_usage", {})
            v1 = await self.call(client, "/revisions/v1/mcp", "benchmark_usage", {})
            v2 = await self.call(client, "/revisions/v2/mcp", "benchmark_usage", {})
            tools = None
            async with streamable_http_client("http://127.0.0.1:8765/revisions/v2/mcp", http_client=client) as (read, write, *_):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    tools = await session.list_tools()
            bad = await client.post("/revisions/not valid/mcp", json={})
            unknown = await client.get("/elsewhere")
            return before, missing, listing, pinned, v1, v2, init, tools, bad, unknown
        before, missing, listing, pinned, v1, v2, init, tools, bad, unknown = self.run_app(scenario)
        self.assertEqual((before[0]["revision"], before[0]["results"][0]["papers"]), ("v1", 1))
        self.assertEqual(missing.status_code, 404)
        self.assertIn("not installed", missing.json()["error"])
        self.assertEqual((listing["pinned"], listing["active"], [r["id"] for r in listing["revisions"]]), ("v1", "v2", ["v1", "v2"]))
        self.assertEqual((pinned[0]["revision"], pinned[0]["results"][0]["papers"]), ("v1", 1))     # /mcp keeps v1
        self.assertEqual((v1[0]["revision"], v1[0]["results"][0]["papers"]), ("v1", 1))
        self.assertEqual((v2[0]["revision"], v2[0]["results"][0]["papers"]), ("v2", 2))
        self.assertIn("fixed by the endpoint path", init.instructions)
        self.assertEqual(len(tools.tools), 6)
        self.assertTrue(all(t.annotations.read_only_hint for t in tools.tools))
        self.assertIn("(data revision v2)", tools.tools[0].description)
        self.assertEqual(bad.status_code, 404)
        self.assertEqual(unknown.status_code, 404)

    def test_overlapping_conversations_on_different_revisions_stay_apart(self):
        data_module.install(self.root, file=str(self.root / "v2.json"))

        async def scenario(client):
            async def one(rid):
                out, _ = await self.call(client, f"/revisions/{rid}/mcp", "benchmark_usage", {})
                return out["revision"] == rid and out["results"][0]["papers"] == (1 if rid == "v1" else 2)
            return await asyncio.gather(*[one("v1" if i % 2 else "v2") for i in range(24)])
        self.assertTrue(all(self.run_app(scenario)))

    def test_damaged_or_removed_revision_reports_recovery_not_other_data(self):
        async def scenario(client):
            d = revisions.revision_dir(self.root, "v1")
            with (d / "benchmark_snapshot.json").open("ab") as fh:
                fh.write(b" ")
            damaged = await self.call(client, "/mcp", "benchmark_usage", {})
            revisions._rmtree(d)
            gone = await client.post("/revisions/v1/mcp", json={})
            return damaged, gone
        damaged, gone = self.run_app(scenario)
        self.assertTrue(damaged[1])
        self.assertIn("failed verification", damaged[0]["error"])
        self.assertEqual(gone.status_code, 404)
        self.assertRegex(gone.json()["error"], "activate|reinstall")       # recovery, never another revision's data


if __name__ == "__main__":
    unittest.main()
