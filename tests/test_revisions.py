"""Immutable revisions: a failed install changes nothing served, concurrent
calls never share a connection, a conversation keeps its revision, the local
MCP server keeps the revision it started with, and repair is a byte-exact
restore from the original manifest, never a rewrite of it."""
import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from bench2agent import cli, data as data_module
from bench2agent import providers
from bench2agent.core import mcp, repository, revisions
from bench2agent.core.benchmarks import BenchmarkStore
from bench2agent.session import Session

QUOTE_V1 = "We evaluate our policies on the Toy benchmark."
QUOTE_V2 = "Toy is used for every evaluation in this revision."
SRC = str(Path(__file__).resolve().parents[1] / "src")


def snapshot(sid, quote, generated_at="2026-01-01"):
    return {"schema_version": 1, "snapshot_id": sid, "generated_at": generated_at,
            "source": "arxiv_html_stated_roles", "benchmarks": [{"id": "toy", "name": "Toy"}],
            "editions": {"icml-2025": {"total_papers": 2, "arxiv_matched": 2, "parsed_papers": 2}},
            "papers": [{"paper_id": "2501.00001", "edition": "icml-2025", "title": "A robot policy",
                        "fields": ["robotics"], "authors": ["Researcher One"],
                        "uses": {"toy": {"roles": {"evaluates_on": [{"text": quote, "where": "experiments"}]}}}},
                       {"paper_id": "2501.00002", "edition": "icml-2025", "title": "Another policy",
                        "fields": ["robotics"], "authors": ["Researcher Two"],
                        "uses": {"toy": {"roles": {"evaluates_on": [{"text": quote, "where": "experiments"}]}}} if sid == "v2" else {}}]}


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.v1 = self.root / "v1.json"
        self.v2 = self.root / "v2.json"
        self.v1.write_text(json.dumps(snapshot("v1", QUOTE_V1)))
        self.v2.write_text(json.dumps(snapshot("v2", QUOTE_V2)))
        self.first = data_module.install(self.root, file=str(self.v1))
        self.served = BenchmarkStore(self.root, backend="sqlite")   # pinned to v1, as a server process would be
        self.patch = patch.object(mcp, "B", self.served)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    # -- publication, pinning, rollback ---------------------------------
    def test_install_publishes_a_complete_revision_and_keeps_old_ones(self):
        self.assertEqual((self.first["revision"], self.first["active"], self.first["outcome"]), ("v1", True, "installed"))
        d = revisions.revision_dir(self.root, "v1")
        self.assertTrue((d / "benchmark_snapshot.json").is_file() and (d / "benchmark_snapshot.sqlite").is_file())
        manifest = json.loads((d / "manifest.json").read_text())
        self.assertEqual(manifest["sqlite_sha256"], repository.file_sha256(d / "benchmark_snapshot.sqlite"))
        self.assertTrue(manifest["sqlite_built_locally"])
        self.assertEqual(manifest["content_sha256"], revisions.content_sha256(json.loads(self.v1.read_text())))
        second = data_module.install(self.root, file=str(self.v2))
        self.assertEqual((revisions.active_revision(self.root), second["revision"]), ("v2", "v2"))
        self.assertTrue(d.is_dir())                                   # v1 stays installed, untouched
        self.assertEqual(self.served.data()["snapshot_id"], "v1")    # a store keeps the revision it was built on
        self.assertEqual(BenchmarkStore(self.root).data()["snapshot_id"], "v2")   # a new store gets the active one
        self.assertEqual(BenchmarkStore(self.root, revision="v1").usage({})["revision"], "v1")
        status = data_module.status(self.root)
        self.assertEqual([(r["id"], r["active"]) for r in status["revisions"]], [("v1", False), ("v2", True)])
        self.assertEqual(status["pointer"], "ok")
        data_module.activate(self.root, "v1")                        # rollback is a verified pointer move
        self.assertEqual(BenchmarkStore(self.root).data()["snapshot_id"], "v1")
        with self.assertRaises(ValueError):
            data_module.activate(self.root, "v9")
        self.assertEqual(revisions.active_revision(self.root), "v1")

    def test_same_content_is_reused_and_different_content_refused(self):
        d = revisions.revision_dir(self.root, "v1")
        manifest_before = (d / "manifest.json").read_bytes()
        again = self.root / "v1-again.json"
        again.write_text(json.dumps(snapshot("v1", QUOTE_V1, generated_at="2026-02-02")))   # a re-export: only generated_at differs
        result = data_module.install(self.root, file=str(again))
        self.assertEqual(result["outcome"], "reused")
        self.assertEqual((d / "manifest.json").read_bytes(), manifest_before)                 # manifest and files kept
        self.assertEqual(json.loads((d / "benchmark_snapshot.json").read_text())["generated_at"], "2026-01-01")
        other = self.root / "v1-other.json"
        other.write_text(json.dumps(snapshot("v1", QUOTE_V2)))
        with self.assertRaisesRegex(ValueError, "different content"):
            data_module.install(self.root, file=str(other))
        self.assertEqual((d / "manifest.json").read_bytes(), manifest_before)

    def test_damaged_copy_is_restored_only_from_matching_bytes(self):
        d = revisions.revision_dir(self.root, "v1")
        manifest_before = (d / "manifest.json").read_bytes()
        original_json = (d / "benchmark_snapshot.json").read_bytes()
        with (d / "benchmark_snapshot.json").open("ab") as fh:
            fh.write(b" ")                                             # damage
        self.assertIn("failed verification", data_module.status(self.root)["verification"])
        with self.assertRaisesRegex(ValueError, "failed verification"):
            data_module.activate(self.root, "v1")
        # same content, different bytes: not accepted as a repair
        again = self.root / "v1-again.json"
        again.write_text(json.dumps(snapshot("v1", QUOTE_V1, generated_at="2026-02-02")))
        with self.assertRaisesRegex(ValueError, "do not match the original checksums"):
            data_module.install(self.root, file=str(again))
        self.assertEqual((d / "manifest.json").read_bytes(), manifest_before)
        # the original bytes: restored, manifest untouched
        result = data_module.install(self.root, file=str(self.v1))
        self.assertEqual(result["outcome"], "restored")
        self.assertEqual((d / "benchmark_snapshot.json").read_bytes(), original_json)
        self.assertEqual((d / "manifest.json").read_bytes(), manifest_before)
        self.assertEqual(data_module.status(self.root)["verification"], "ok")

    def test_failed_install_leaves_the_active_revision_consistent(self):
        d = revisions.revision_dir(self.root, "v1")
        before = {p.name: p.read_bytes() for p in d.iterdir()}

        def crashing_publish():
            with patch.object(revisions, "set_active", side_effect=OSError("disk full")):
                data_module.install(self.root, file=str(self.v2))
        failures = [
            lambda: data_module.install(self.root, file=str(self.v2), sha256="0" * 64),
            lambda: data_module.install(self.root, file=str(self.root / "missing.json")),
            crashing_publish,
        ]
        for fail in failures:
            with self.assertRaises((ValueError, OSError)):
                fail()
            self.assertEqual(revisions.active_revision(self.root), "v1")
            self.assertEqual({p.name: p.read_bytes() for p in d.iterdir()}, before)
            status = data_module.status(self.root)
            self.assertTrue(status["sqlite"]["matches_json"])
            self.assertEqual((status["snapshot_id"], BenchmarkStore(self.root).usage({})["snapshot_id"]), ("v1", "v1"))
            self.assertEqual([p.name for p in revisions.revisions_dir(self.root).glob(".staging-*")], [])
        # the crashed publish left v2 installed but inactive; activating it is explicit
        self.assertEqual([r["id"] for r in revisions.list_revisions(self.root) if not r["active"]], ["v2"])

    def test_pointer_missing_and_pointer_broken_are_different(self):
        pointer = revisions.revisions_dir(self.root) / "active"
        pointer.unlink()
        status = data_module.status(self.root)
        self.assertEqual((status["pointer"], status["available"]), ("none", False))
        self.assertIn("data install", status["reason"])
        self.assertIsNone(revisions.active_revision(self.root))
        self.assertFalse(BenchmarkStore(self.root).data()["available"])
        pointer.write_text("v9\n")
        status = data_module.status(self.root)
        self.assertEqual((status["pointer"], status["available"]), ("broken", False))
        self.assertIn("activate", status["reason"])
        with self.assertRaises(revisions.RevisionError):
            revisions.active_revision(self.root)
        self.assertIn("v9", BenchmarkStore(self.root).usage({})["error"])      # no fallback to older files
        with patch.object(data_module, "install") as install:
            with self.assertRaisesRegex(ValueError, "activate"):
                cli.require_data(self.root, interactive=True)
        install.assert_not_called()                                              # never downloaded over a broken install
        with self.assertRaisesRegex(ValueError, "not installed"):
            revisions.paths_for(self.root, "v9")
        self.assertIn("not installed", BenchmarkStore(self.root, revision="v9").usage({})["error"])
        data_module.activate(self.root, "v1")
        self.assertEqual(data_module.status(self.root)["pointer"], "ok")

    def test_checksum_cache_distinguishes_files_with_equal_size_and_mtime(self):
        data_module.install(self.root, file=str(self.v2))
        a, _, _ = revisions.paths_for(self.root, "v1")
        b, _, _ = revisions.paths_for(self.root, "v2")
        # pad v1's JSON copy so the two files share size, then share mtime
        pad = self.root / "same-stamp.json"
        text_b = b.read_text()
        text_a = a.read_text()
        longer, shorter = (text_a, text_b) if len(text_a) >= len(text_b) else (text_b, text_a)
        pad.write_text(shorter + " " * (len(longer) - len(shorter)))
        other = a if longer is text_a else b
        os.utime(pad, ns=(other.stat().st_mtime_ns, other.stat().st_mtime_ns))
        self.assertEqual((pad.stat().st_size, pad.stat().st_mtime_ns), (other.stat().st_size, other.stat().st_mtime_ns))
        store = BenchmarkStore(self.root, backend="auto")
        self.assertNotEqual(store._json_sha(other), store._json_sha(pad))

    # -- isolation --------------------------------------------------------
    def test_concurrent_calls_share_no_connection(self):
        expected = {name: json.dumps(getattr(self.served, name)(dict(args)), sort_keys=True) for name, args in [
            ("usage", {}), ("evidence", {"benchmark": "toy"}), ("trend", {"benchmark": "toy"}), ("scope", {})]}

        def work(i):
            name, args = [("usage", {}), ("evidence", {"benchmark": "toy"}), ("trend", {"benchmark": "toy"}), ("scope", {})][i % 4]
            with self.served.pinned():              # hold a session open while others run
                out = json.dumps(getattr(self.served, name)(dict(args)), sort_keys=True)
                self.served.verify("2501.00001", QUOTE_V1)
            return out == expected[name]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(work, range(64)))
        self.assertTrue(all(results))

    def test_overlapping_async_requests_each_keep_their_revision(self):
        data_module.install(self.root, file=str(self.v2))
        stores = {"v1": BenchmarkStore(self.root, revision="v1"), "v2": BenchmarkStore(self.root, revision="v2")}

        async def request(rid):
            with mcp.using(stores[rid]):
                await asyncio.sleep(0)                               # another request runs on this thread meanwhile
                seen = mcp.store().revision
                await asyncio.sleep(0)
                usage = next(t for t in mcp._benchmark_tools() if t["name"] == "benchmark_usage")["fn"]({})
                await asyncio.sleep(0)
                return seen == rid and usage["revision"] == rid and usage["results"][0]["papers"] == (1 if rid == "v1" else 2)

        async def main():
            return await asyncio.gather(*[request("v1" if i % 2 else "v2") for i in range(20)])
        self.assertTrue(all(asyncio.run(main())))
        self.assertIs(mcp.store(), mcp.B)                              # nothing leaks outside the requests

    # -- conversations ----------------------------------------------------
    def scripted(self, text):
        return [
            {"status": "completed", "output": [{"type": "function_call", "call_id": "c1", "name": "benchmark_usage", "arguments": "{}"}]},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]},
        ]

    def test_conversation_keeps_its_revision_after_a_new_one_is_published(self):
        session = Session(self.root, "openai", "fixture-model")
        seen = []

        def post(provider, path, payload):
            seen.append(copy.deepcopy(payload))
            return script[len(seen) - 1]
        script = self.scripted("Count ⟦benchmark_usage:{}|1 paper⟧ and ⟦arxiv:2501.00001|" + QUOTE_V1 + "⟧")
        with patch.object(providers, "post", side_effect=post):
            first = session.ask("What is used?", lambda _: None)
        self.assertEqual((first["on"]["benchmarks"]["snapshot_id"], first["verified"]["passed"], first["verified"]["fpassed"]), ("v1", 1, 1))
        data_module.install(self.root, file=str(self.v2))                 # a new revision goes live
        self.assertEqual(BenchmarkStore(self.root).data()["snapshot_id"], "v2")
        seen.clear()
        script = self.scripted("Still ⟦benchmark_usage:{}|1 paper⟧ and ⟦arxiv:2501.00001|" + QUOTE_V1 + "⟧ not ⟦arxiv:2501.00002|" + QUOTE_V2 + "⟧")
        resumed = Session(self.root, "openai", "fixture-model", cid=session.doc["id"])
        with patch.object(providers, "post", side_effect=post):
            second = resumed.ask("And now?", lambda _: None)
        # the tool call inside the turn, the figure check and the quote check all read v1
        tool_output = json.loads(seen[1]["input"][-1]["output"])
        self.assertEqual((tool_output["snapshot_id"], tool_output["revision"], tool_output["results"][0]["papers"]), ("v1", "v1", 1))
        self.assertEqual(second["on"]["benchmarks"]["snapshot_id"], "v1")
        self.assertEqual((second["verified"]["passed"], second["verified"]["checked"], second["verified"]["fpassed"]), (1, 2, 1))
        self.assertIs(mcp.store(), mcp.B)                                 # nothing pinned outside the turn
        fresh = Session(self.root, "openai", "fixture-model")              # a fresh conversation gets the new revision
        seen.clear()
        script = self.scripted("Now ⟦benchmark_usage:{}|2 papers⟧")
        with patch.object(providers, "post", side_effect=post):
            turn = fresh.ask("What is used?", lambda _: None)
        self.assertEqual((turn["on"]["benchmarks"]["snapshot_id"], turn["verified"]["fpassed"]), ("v2", 1))
        revisions._rmtree(revisions.revision_dir(self.root, "v1"))        # once the old revision is gone, say so
        with self.assertRaisesRegex(ValueError, "dataset changed"):
            Session(self.root, "openai", "fixture-model", cid=session.doc["id"]).ask("Again?", lambda _: None)

    def test_a_publish_in_the_middle_of_a_question_does_not_reach_that_question(self):
        session = Session(self.root, "openai", "fixture-model")
        seen = []
        script = self.scripted("Count ⟦benchmark_usage:{}|1 paper⟧")

        def post(provider, path, payload):
            seen.append(copy.deepcopy(payload))
            if len(seen) == 2:                                            # between the tool call and the final answer
                data_module.install(self.root, file=str(self.v2))
            return script[len(seen) - 1]
        with patch.object(providers, "post", side_effect=post):
            turn = session.ask("What is used?", lambda _: None)
        tool_output = json.loads(seen[1]["input"][-1]["output"])
        self.assertEqual((tool_output["revision"], turn["on"]["benchmarks"]["snapshot_id"], turn["verified"]["fpassed"]), ("v1", "v1", 1))
        self.assertEqual(session.doc["snapshot_id"], "v1")
        self.assertEqual(revisions.active_revision(self.root), "v2")
        self.assertEqual(Session(self.root, "openai", "fixture-model").store().revision, "v2")

    # -- the local stdio MCP server -----------------------------------------
    def mcp_process(self, *extra):
        env = {**os.environ, "PYTHONPATH": SRC}
        env.pop("BENCH2AGENT_STORE", None)
        return subprocess.Popen([sys.executable, "-m", "bench2agent", "--home", str(self.root), "mcp", *extra],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    @staticmethod
    def call(proc, msg_id):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
                                     "params": {"name": "benchmark_usage", "arguments": {}}}) + "\n")
        proc.stdin.flush()
        return json.loads(json.loads(proc.stdout.readline())["result"]["content"][0]["text"])

    def test_stdio_server_keeps_its_revision_until_restarted(self):
        self.patch.stop()
        server = self.mcp_process()
        self.addCleanup(server.kill)
        server.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}) + "\n")
        server.stdin.flush()
        init = json.loads(server.stdout.readline())["result"]
        self.assertIn("revision v1", init["instructions"])
        self.assertEqual(self.call(server, 2)["revision"], "v1")
        data_module.install(self.root, file=str(self.v2))                 # published while the server runs
        self.assertEqual(self.call(server, 3)["revision"], "v1")           # the running process keeps v1
        server.stdin.close()
        self.assertEqual(server.wait(timeout=20), 0)
        restarted = self.mcp_process()                                     # a restart serves the active revision
        self.addCleanup(restarted.kill)
        self.assertEqual(self.call(restarted, 1)["revision"], "v2")
        restarted.stdin.close()
        restarted.wait(timeout=20)
        pinned = self.mcp_process("--revision", "v1")                      # reproduce earlier answers on request
        self.addCleanup(pinned.kill)
        self.assertEqual(self.call(pinned, 1)["results"][0]["papers"], 1)
        pinned.stdin.close()
        pinned.wait(timeout=20)
        missing = self.mcp_process("--revision", "v9")
        _, err = missing.communicate(timeout=20)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("not installed", err)
        self.patch.start()


if __name__ == "__main__":
    unittest.main()
