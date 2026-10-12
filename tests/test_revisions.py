"""Immutable revisions: a failed install changes nothing served, concurrent
calls never share a connection, and a conversation keeps its revision."""
import copy
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from bench2agent import data as data_module
from bench2agent import providers
from bench2agent.core import mcp, repository, revisions
from bench2agent.core.benchmarks import BenchmarkStore
from bench2agent.session import Session

QUOTE_V1 = "We evaluate our policies on the Toy benchmark."
QUOTE_V2 = "Toy is used for every evaluation in this revision."


def snapshot(sid, quote):
    return {"schema_version": 1, "snapshot_id": sid, "generated_at": "2026-01-01",
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
        self.served = BenchmarkStore(self.root, backend="sqlite")
        self.patch = patch.object(mcp, "B", self.served)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_install_publishes_a_complete_revision_and_keeps_old_ones(self):
        self.assertEqual((self.first["revision"], self.first["active"]), ("v1", True))
        d = revisions.revision_dir(self.root, "v1")
        self.assertTrue((d / "benchmark_snapshot.json").is_file() and (d / "benchmark_snapshot.sqlite").is_file())
        manifest = json.loads((d / "manifest.json").read_text())
        self.assertEqual(manifest["sqlite_sha256"], repository.file_sha256(d / "benchmark_snapshot.sqlite"))
        second = data_module.install(self.root, file=str(self.v2))
        self.assertEqual((revisions.active_revision(self.root), second["revision"]), ("v2", "v2"))
        self.assertTrue(d.is_dir())                                   # v1 stays installed, untouched
        self.assertEqual(self.served.data()["snapshot_id"], "v2")
        self.assertEqual(BenchmarkStore(self.root, revision="v1").data()["snapshot_id"], "v1")
        status = data_module.status(self.root)
        self.assertEqual([(r["id"], r["active"]) for r in status["revisions"]], [("v1", False), ("v2", True)])
        # rollback is a pointer move
        data_module.activate(self.root, "v1")
        self.assertEqual(self.served.data()["snapshot_id"], "v1")
        with self.assertRaises(ValueError):
            data_module.activate(self.root, "v9")
        # the same id with different bytes is refused
        other = self.root / "v1-other.json"
        other.write_text(json.dumps(snapshot("v1", QUOTE_V2)))
        with self.assertRaisesRegex(ValueError, "different content"):
            data_module.install(self.root, file=str(other))
        self.assertEqual(self.served.data()["snapshot_id"], "v1")

    def test_failed_install_leaves_the_active_revision_consistent(self):
        d = revisions.revision_dir(self.root, "v1")
        before = {p.name: p.read_bytes() for p in d.iterdir()}
        failures = [
            lambda: data_module.install(self.root, file=str(self.v2), sha256="0" * 64),
            lambda: data_module.install(self.root, file=str(self.root / "missing.json")),
        ]
        # a crash between the two files being staged and the pointer moving
        def crashing_publish():
            with patch.object(revisions, "set_active", side_effect=OSError("disk full")):
                data_module.install(self.root, file=str(self.v2))
        failures.append(crashing_publish)
        for fail in failures:
            with self.assertRaises((ValueError, OSError)):
                fail()
            self.assertEqual(revisions.active_revision(self.root), "v1")
            self.assertEqual({p.name: p.read_bytes() for p in d.iterdir()}, before)
            status = data_module.status(self.root)
            self.assertTrue(status["sqlite"]["matches_json"])
            self.assertEqual((status["snapshot_id"], self.served.usage({})["snapshot_id"]), ("v1", "v1"))
            self.assertEqual([p.name for p in revisions.revisions_dir(self.root).glob(".staging-*")], [])
        # the crashed publish left v2 installed but inactive; activating it is explicit
        self.assertEqual([r["id"] for r in revisions.list_revisions(self.root) if not r["active"]], ["v2"])

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
        self.assertEqual(mcp.B.data()["snapshot_id"], "v2")
        seen.clear()
        script = self.scripted("Still ⟦benchmark_usage:{}|1 paper⟧ and ⟦arxiv:2501.00001|" + QUOTE_V1 + "⟧ not ⟦arxiv:2501.00002|" + QUOTE_V2 + "⟧")
        resumed = Session(self.root, "openai", "fixture-model", cid=session.doc["id"])
        with patch.object(providers, "post", side_effect=post):
            second = resumed.ask("And now?", lambda _: None)
        # the tool call inside the turn, the figure check and the quote check all read v1
        tool_output = json.loads(seen[1]["input"][-1]["output"])
        self.assertEqual((tool_output["snapshot_id"], tool_output["results"][0]["papers"]), ("v1", 1))
        self.assertEqual(second["on"]["benchmarks"]["snapshot_id"], "v1")
        self.assertEqual((second["verified"]["passed"], second["verified"]["checked"], second["verified"]["fpassed"]), (1, 2, 1))
        self.assertEqual(mcp.store(), mcp.B)                              # nothing pinned outside the turn
        # a fresh conversation gets the new revision
        fresh = Session(self.root, "openai", "fixture-model")
        seen.clear()
        script = self.scripted("Now ⟦benchmark_usage:{}|2 papers⟧")
        with patch.object(providers, "post", side_effect=post):
            turn = fresh.ask("What is used?", lambda _: None)
        self.assertEqual((turn["on"]["benchmarks"]["snapshot_id"], turn["verified"]["fpassed"]), ("v2", 1))
        # once the old revision is gone, the old conversation says so instead of mixing
        revisions._rmtree(revisions.revision_dir(self.root, "v1"))
        with self.assertRaisesRegex(ValueError, "dataset changed"):
            Session(self.root, "openai", "fixture-model", cid=session.doc["id"]).ask("Again?", lambda _: None)


if __name__ == "__main__":
    unittest.main()
