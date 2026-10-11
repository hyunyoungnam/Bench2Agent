"""SQLite import and query parity: the same contract, the same answers.

The fixture is built to contain every case the migration must preserve:
homonyms (two Mirage products, attributed only by citation), aliases, an
unknown author list, a zero-adoption introduction, unresolved use, a paper
observed in two editions, unlabelled observations, pre-claim use, a registered
benchmark with a location and no claim, and a homepage-only location.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bench2agent import data as data_module
from bench2agent.core import repository
from bench2agent.core.benchmarks import BenchmarkStore, build_snapshot


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.write("config/benchmarks.json", {"checked": "2026-09-17", "benchmarks": [
            {"id": "toy", "name": "Toy", "aliases": ["Toy Suite"], "gh": "lab/toy", "ok": {"gh": True}},
            {"id": "mirage-old", "name": "Mirage", "url": "https://mirage.example.org"},
            {"id": "bare", "name": "Bare", "ambiguous": True, "note": "also a word"}]})

        def intro(bid, name, pid, title, **extra):
            return {"id": bid, "name": name, "fold": name.lower(), "kind": "benchmark",
                    "first_claim": "icml-2025", "developers": ["Alice Smith"],
                    "claims": [{"claim_id": pid + ":" + bid, "arxiv_base": pid, "edition": "icml-2025", "title": title,
                                "noun": "benchmark", "label": "E",
                                "evidence": "We introduce " + name + " for evaluating robotic policies."}], **extra}
        self.write("config/benchmarks_introduced.json", {"benchmarks": [
            intro("fresh", "Fresh", "2501.00001", "A fresh evaluation protocol"),
            intro("unused", "Unused", "2501.00001", "A fresh evaluation protocol"),
            intro("mirage-new", "Mirage", "2501.00005", "Mirage: evaluating robotic policies",
                  homonym_of_registered="mirage-old"),
            {**intro("traindata", "TrainData", "2501.00006", "TrainData: a corpus"), "kind": "dataset"}]})

        def use(key, role="evaluates_on", text=None, **extra):
            return {key: {"roles": {role: [{"where": "experiments", "text": text or
                         "We evaluate our policies on the " + key + " benchmark."}]},
                         "surfaces": {key.title(): 2}, **extra}}
        both = use("fresh")
        both["fresh"]["roles"].update(use("fresh", "trains_on")["fresh"]["roles"])
        table = use("toy", text="Toy | 91.2 | 88.0")
        table["toy"]["roles"]["evaluates_on"][0]["where"] = "table:cell"
        old = [
            ("2401.00001", ["Bob Lee"], ["robotics"], use("toy")),
            ("2401.00002", ["Bob Lee"], ["robotics"], use("mirage-old")),
            ("2401.00003", ["Bob Lee"], [], {}),
            ("2401.00004", ["Bob Lee"], ["robotics"], use("fresh")),           # pre-claim use
            ("2401.00005", ["Carol Wu"], ["Vision"], table)]
        new = [
            ("2501.00001", ["Alice Smith"], ["robotics"], use("fresh")),       # introducing paper
            ("2501.00002", ["Bob Lee"], ["robotics"], both),
            ("2501.00003", ["Alice Smith"], ["robotics"], use("fresh")),       # self use
            ("2501.00004", [], ["robotics"], use("fresh")),                    # unknown authors
            ("2501.00005", ["Alice Smith"], ["robotics"], use("mirage-old", role_cites=[
                "Mirage: evaluating robotic policies. Alice Smith et al."])),  # homonym, attributed by citation
            ("2501.00006", ["Bob Lee"], ["robotics", "Vision"], use("toy")),
            ("2501.00007", ["Bob Lee"], [], {}),
            ("2401.00005", ["Carol Wu"], ["Vision"], use("toy")),             # same paper, second edition
            ("2501.00008", ["Dan Ito"], ["robotics"], use("bare"))]
        for year, records in ((2024, old), (2025, new)):
            ed = f"icml-{year}"
            self.write(f"data/processed/papers_{year}.jsonl", [
                {"event_id": i, "title": "Paper " + pid, "authors": authors,
                 **({"subarea": fields[0]} if fields else {})}
                for i, (pid, authors, fields, _) in enumerate(records)], lines=True)
            self.write(f"data/processed/topics_{ed}.json", {"topics": [
                {"label": f, "explicit": [i for i, (_, _, fs, _) in enumerate(records) if f in fs]}
                for f in ("robotics", "Vision")]})
            self.write(f"data/raw/arxiv/resolved_{ed}.jsonl", [
                {"arxiv_base": pid, "event_id": i} for i, (pid, _, _, _) in enumerate(records)], lines=True)
            self.write(f"data/interim/mentions/mentions_{ed}.jsonl", [
                {"arxiv_base": pid, "mentions": {**uses, "nobody": {"as_written": "Nobody", "roles": {"evaluates_on": [
                    {"where": "experiments", "text": "We also test on Nobody."}]}}} if pid == "2501.00008" else uses}
                for pid, _, _, uses in records], lines=True)
        build_snapshot(self.root)
        self.json_path = self.root / "data/processed/benchmark_snapshot.json"
        self.db_path = self.json_path.with_name(repository.DATABASE)
        self.report = repository.import_snapshot(self.json_path, self.db_path)
        self.json = BenchmarkStore(self.root, backend="json")
        self.sqlite = BenchmarkStore(self.root, backend="sqlite")

    def write(self, name, obj, lines=False):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(map(json.dumps, obj)) if lines else json.dumps(obj))


CALLS = [
    ("scope", {}), ("scope", {"query": "robot"}),
    ("usage", {}), ("usage", {"latest": False}), ("usage", {"topic": "robotics"}), ("usage", {"topic": "Vision", "latest": False}),
    ("usage", {"role": "trains_on", "latest": False}), ("usage", {"limit": 1}), ("usage", {"venue": "icml", "years": [2024]}),
    ("usage", {"paper_ids": ["2401.00005", "2501.00006"], "latest": False}), ("usage", {"edition": "icml-2024"}),
    ("trend", {"benchmark": "toy"}), ("trend", {"benchmark": "fresh"}), ("trend", {"benchmark": "fresh", "role": "trains_on"}),
    ("trend", {"benchmark": "mirage-old"}), ("trend", {"benchmark": "Toy Suite"}), ("trend", {"benchmark": "bare", "topic": "robotics"}),
    ("evidence", {"benchmark": "fresh"}), ("evidence", {"benchmark": "fresh", "limit": 1}), ("evidence", {"benchmark": "fresh", "offset": 1, "limit": 2}),
    ("evidence", {"benchmark": "toy", "edition": "icml-2025"}), ("evidence", {"benchmark": "mirage-new"}),
    ("adoption", {"benchmark": "fresh"}), ("adoption", {"benchmark": "unused"}), ("adoption", {"benchmark": "mirage-new"}),
    ("adoption", {"benchmark": "fresh", "topic": "robotics"}), ("adoption", {"benchmark": "traindata"}),
    ("adoption", {"benchmark": "toy"}),   # no claim: an error object, not an exception
    ("new", {}), ("new", {"sort": "adoption"}), ("new", {"include_adoption": True, "limit": 2, "offset": 1}),
    ("new", {"kind": "dataset", "include_adoption": True}), ("new", {"topic": "robotics"}), ("new", {"edition": "icml-2025"}),
]
ERRORS = [("trend", {"benchmark": "Mirage"}), ("usage", {"topic": "nowhere"}), ("usage", {"role": "cites"}),
          ("usage", {"venue": "corl"}), ("evidence", {"benchmark": "missing"})]


class ParityTests(Fixture):
    def call(self, store, name, args):
        try:
            return getattr(store, name)(dict(args))
        except ValueError as exc:
            return {"ValueError": str(exc)}

    def test_every_tool_matches_between_backends(self):
        for name, args in CALLS + ERRORS:
            with self.subTest(tool=name, args=args):
                a, b = self.call(self.json, name, args), self.call(self.sqlite, name, args)
                self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        for name, args in ERRORS:
            self.assertIn("ValueError", self.call(self.sqlite, name, args))

    def test_cases_are_present_and_computed_the_same(self):
        usage = self.sqlite.usage({"latest": False, "limit": 50})
        self.assertEqual(usage["coverage"]["parsed_papers"], 14)          # two unlabelled no-use papers included
        rows = {r["id"]: r for r in usage["results"]}
        self.assertEqual(rows["toy"]["papers"], 4)                          # 2401.00005 counted in both editions
        self.assertEqual(rows["toy"]["locations"][0]["checked_at"], "2026-09-17")
        self.assertNotIn("mirage-old", rows)                                # unresolved homonym use is not a count
        self.assertEqual(usage["coverage"]["unattributed_papers"], 2)       # 2401.00002 (homonym) and 2501.00008 (Nobody)
        adoption = self.sqlite.adoption({"benchmark": "fresh"})
        self.assertEqual((adoption["evaluates_on"]["others"], adoption["evaluates_on"]["self"],
                          adoption["evaluates_on"]["undetermined"], adoption["before_claim"]), (1, 1, 1, 1))
        self.assertEqual(self.sqlite.adoption({"benchmark": "unused"})["evaluates_on"]["others"], 0)
        # the cited homonym use is attributed to mirage-new (a usage row of its
        # own) but it is the introducing paper, so adoption excludes it
        self.assertEqual(rows["mirage-new"]["papers"], 1)
        self.assertEqual(self.sqlite.adoption({"benchmark": "mirage-new"})["evaluates_on"], {"others": 0, "self": 0, "undetermined": 0})
        self.assertEqual(self.sqlite.verify("2501.00002", "We evaluate our policies on the fresh benchmark."),
                         self.json.verify("2501.00002", "We evaluate our policies on the fresh benchmark."))
        self.assertIsNone(self.sqlite.verify("2401.00005", "Toy | 91.2 | 88.0"))   # table cells are not quotations
        self.assertEqual(self.sqlite.verify("2501.00001", "We introduce Fresh for evaluating robotic policies.")["edition"], "icml-2025")

    def test_round_trip_is_exact_and_identity_is_kept_apart(self):
        source = json.loads(self.json_path.read_text())
        self.assertEqual(repository.export_snapshot(self.db_path), source)
        con = sqlite3.connect(self.db_path)
        meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta")}
        con.close()
        self.assertEqual(meta["snapshot_id"], source["snapshot_id"])
        self.assertEqual(meta["db_schema_version"], repository.DB_SCHEMA_VERSION)
        self.assertEqual(meta["source_sha256"], repository.file_sha256(self.json_path))
        self.assertEqual(self.report["sha256"], repository.file_sha256(self.db_path))
        self.assertEqual((self.db_path.with_name(self.db_path.name + ".sha256")).read_text().split()[0], self.report["sha256"])
        self.assertEqual(self.report["uses"], sum(len(p["uses"]) for p in source["papers"]))
        self.assertEqual(self.report["evidence"], sum(len(evs) for p in source["papers"] for u in p["uses"].values() for evs in u["roles"].values()))
        self.assertEqual(self.report["unattributed"], sum(len(p["unattributed"]) for p in source["papers"]))
        self.assertNotIn("imported_at", meta)   # no invented dates
        self.assertEqual(self.sqlite.data()["registry_checked"], "2026-09-17")

    def test_reimport_is_idempotent_and_failure_keeps_the_old_file(self):
        before = self.db_path.read_bytes()
        again = repository.import_snapshot(self.json_path, self.db_path)
        self.assertEqual(again["sha256"], self.report["sha256"])
        self.assertEqual(self.db_path.read_bytes(), before)
        broken = self.root / "broken.json"
        broken.write_text(json.dumps({**json.loads(self.json_path.read_text()), "source": "abstracts"}))
        with self.assertRaises(ValueError):
            repository.import_snapshot(broken, self.db_path)
        bad = json.loads(self.json_path.read_text())
        bad["papers"][0]["uses"]["ghost"] = {"surfaces": {}, "roles": {}}   # a use of an unknown benchmark id
        broken.write_text(json.dumps(bad))
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            repository.import_snapshot(broken, self.db_path)
        self.assertEqual(self.db_path.read_bytes(), before)
        self.assertEqual([p.name for p in self.db_path.parent.glob(".import-*")], [])

    def test_backend_selection_and_status(self):
        self.assertEqual(self.sqlite.data()["backend"], "sqlite")
        self.assertEqual(self.json.data()["backend"], "json")
        auto = BenchmarkStore(self.root, backend="auto")
        self.assertEqual(auto.data()["backend"], "sqlite")
        status = data_module.status(self.root)
        self.assertTrue(status["sqlite"]["matches_json"])
        self.assertEqual(status["sqlite"]["paper_observations"], 14)
        self.db_path.unlink()
        self.assertEqual(BenchmarkStore(self.root, backend="auto").data()["backend"], "json")
        missing = BenchmarkStore(self.root, backend="sqlite")
        self.assertFalse(missing.data()["available"])
        self.assertIn("data sqlite", missing.usage({})["error"])
        self.assertFalse(data_module.status(self.root)["sqlite"]["available"])
        with self.assertRaises(ValueError):
            BenchmarkStore(self.root, backend="postgres")
        os.environ["BENCH2AGENT_STORE"] = "auto"
        try:
            self.assertEqual(BenchmarkStore(self.root).backend, "auto")
        finally:
            del os.environ["BENCH2AGENT_STORE"]


if __name__ == "__main__":
    unittest.main()
