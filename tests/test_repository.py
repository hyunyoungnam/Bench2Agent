"""The revision database: built directly from the extraction, identified by
its content, served by the one SQLite repository, installed from bundles,
bare files and legacy JSON.

The fixture is built to contain every case the storage must preserve:
homonyms (two Mirage products, attributed only by citation), aliases, an
unknown author list, a zero-adoption introduction, unresolved use, a paper
observed in two editions, unlabelled observations, pre-claim use, a registered
benchmark with a location and no claim, and a homepage-only location.
"""
import json
import os
import sqlite3
import tarfile
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from bench2agent import data as data_module
from bench2agent.core import repository, revisions
from bench2agent.core.benchmarks import BenchmarkStore, build_revision


OLD_META_KEYS = ["db_schema_version", "generated_at", "registry_checked", "schema_version", "snapshot_id", "source", "source_sha256"]
PUBLIC_BUNDLE = Path(os.environ.get("BENCH2AGENT_TEST_BUNDLE") or Path(__file__).resolve().parents[1] / "dist/bench2agent-data-20261013.tar.gz")
PUBLIC_V1_SHA256 = "9195fce6afd7e4ae7448be67bd9a6f17e5374bbbbe602f3900737088f69d3af2"   # data-20261013, no longer the default
RELEASE_BUNDLE = Path(os.environ.get("BENCH2AGENT_TEST_RELEASE_BUNDLE") or Path(__file__).resolve().parents[1] / "data/releases/data-20261021/bench2agent-data.tar.gz")


def legacy_database(json_path: Path, db_path: Path) -> None:
    """A database as 0.4.0's importer wrote it: the same tables, no
    revision_id, content hash, hash rule or min_client_version in `meta`."""
    repository.import_snapshot(json_path, db_path)
    con = sqlite3.connect(db_path)
    con.execute("DELETE FROM meta WHERE key IN ('revision_id', 'content_sha256', 'content_hash_rule', 'min_client_version')")
    con.commit()
    con.execute("VACUUM")
    con.close()
    db_path.with_name(db_path.name + ".sha256").write_text(f"{repository.file_sha256(db_path)}  {db_path.name}\n")


def v1_bundle(data: dict, directory: Path, bundle: Path, content_sha256: str | None = None) -> dict:
    """A data-v1 bundle as 0.4.0 shipped it: JSON + the database 0.4.0 built
    from it + a v1 manifest whose content hash is the old definition."""
    directory.mkdir(parents=True, exist_ok=True)
    js, db = directory / "benchmark_snapshot.json", directory / "benchmark_snapshot.sqlite"
    js.write_text(json.dumps(data))
    legacy_database(js, db)
    manifest = {"format": "bench2agent-data-v1", "snapshot_id": data["snapshot_id"], "source": repository.SOURCE,
                "sha256": repository.file_sha256(js), "sqlite_sha256": repository.file_sha256(db), "db_schema_version": 1,
                "content_sha256": content_sha256 or repository.content_hash(data, "v1-json"), "generated_at": data["generated_at"]}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with tarfile.open(bundle, "w:gz") as archive:
        archive.add(js, arcname=data_module.MEMBER)
        archive.add(db, arcname=data_module.DB_MEMBER)
        archive.add(directory / "manifest.json", arcname="manifest.json")
    return manifest


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
            intro("mirage-new", "Mirage", "2501.00005", "Mirage: evaluating robotic policies",
                  homonym_of_registered="mirage-old"),
            {**intro("traindata", "TrainData", "2501.00006", "TrainData: a corpus"), "kind": "dataset"},
            {**intro("unused", "Unused", "2501.00001", "A fresh evaluation protocol"), "homonym_of": ["unused-2"]},
            # a second product under the name Unused, by another paper: an identity candidate, never merged
            {**intro("unused-2", "Unused", "2501.00010", "Unused again: a second product under the name"), "homonym_of": ["unused"],
             "identity_candidates": [{"id": "unused", "relations": ["shared author"]}]}]})

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
            ("2501.00007", ["Bob Lee"], [], use("unused")),                   # a name two products carry: unattributed
            ("2401.00005", ["Carol Wu"], ["Vision"], use("toy")),             # same paper, second edition
            ("2501.00008", ["Dan Ito"], ["robotics"], use("bare"))]
        self.records = {2024: old, 2025: new}
        self.write_corpus()
        self.built = build_revision(self.root)
        self.db_built = Path(self.built["path"])
        self.first = data_module.install(self.root, file=str(self.db_built))   # registers the build as the served revision
        self.db_path, self.rid = revisions.paths_for(self.root)
        self.store = BenchmarkStore(self.root)

    def write_corpus(self):
        for year, records in self.records.items():
            ed = f"icml-{year}"
            self.write(f"data/processed/papers_{year}.jsonl", [
                {"event_id": i, "title": "Paper " + pid, "authors": authors,
                 # 2501.00007's topic file entry is keyed by its merged Oral row 700, not its representative
                 **({"duplicate_event_ids": [700]} if pid == "2501.00007" else {}),
                 # declared subarea for most; the last two papers of 2025 carry a venue track and a volume id
                 **({"subarea": fields[0], "topic_origin": "declared"} if fields and pid not in ("2401.00005", "2501.00008") else {}),
                 # a topic whose origin was never recorded (a file older than topic_origin, not in the verified table)
                 **({"area": "Mystery Area"} if pid == "2401.00003" else {}),
                 **({"area": "AAAI Technical Track on Computer Vision II", "topic_raw": "AAAI Technical Track on Computer Vision II",
                     "topic_origin": "venue_track"} if pid == "2401.00005" else {}),
                 **({"area": "2025.acl-long", "topic_raw": "2025.acl-long", "topic_origin": "venue_volume"} if pid == "2501.00008" else {})}
                for i, (pid, authors, fields, _) in enumerate(records)], lines=True)
            self.write(f"data/processed/topics_{ed}.json", {"topics": [
                {"label": f, "explicit": [i for i, (_, _, fs, _) in enumerate(records) if f in fs]}
                for f in ("robotics", "Vision")] + ([{"label": "merged-row topic", "explicit": [700]}] if year == 2025 else [])})
            self.write(f"data/raw/arxiv/resolved_{ed}.jsonl", [
                {"arxiv_base": pid, "event_id": i} for i, (pid, _, _, _) in enumerate(records)], lines=True)
            self.write(f"data/interim/mentions/mentions_{ed}.jsonl", [
                {"arxiv_base": pid, "mentions": {**uses, "nobody": {"as_written": "Nobody", "roles": {"evaluates_on": [
                    {"where": "experiments", "text": "We also test on Nobody."}]}}} if pid == "2501.00008" else uses}
                for pid, _, _, uses in records] + ([
                # parsed text whose conference paper is no longer identified (an arXiv collision): no observation
                {"arxiv_base": "2501.99999", "mentions": {"toy": {"roles": {"evaluates_on": [
                    {"where": "experiments", "text": "We evaluate on the toy benchmark."}]}, "surfaces": {"Toy": 1}}}}]
                if year == 2025 else []), lines=True)

    def write(self, name, obj, lines=False):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(map(json.dumps, obj)) if lines else json.dumps(obj))

    def call(self, store, name, args):
        try:
            return getattr(store, name)(dict(args))
        except ValueError as exc:
            return {"ValueError": str(exc)}


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
IDENTITY = ("snapshot_id", "revision", "generated_at")


class StorageTests(Fixture):
    def test_expected_figures(self):
        usage = self.store.usage({"latest": False, "limit": 50})
        self.assertEqual(usage["coverage"]["parsed_papers"], 14)          # two unlabelled no-use papers included
        self.assertEqual(usage["coverage"]["editions"]["icml-2025"]["parsed_unidentified"], 1)   # the collision row: counted, not an observation
        self.assertNotIn("parsed_unidentified", usage["coverage"]["editions"]["icml-2024"])
        self.assertEqual(usage["coverage"]["field_labelled_papers"], 13)   # 2501.00008: its volume id is no field, its derived topic is
        rows = {r["id"]: r for r in usage["results"]}
        self.assertEqual(rows["toy"]["papers"], 4)                          # 2401.00005 counted in both editions
        self.assertEqual(rows["toy"]["locations"][0]["checked_at"], "2026-09-17")
        self.assertNotIn("mirage-old", rows)                                # unresolved homonym use is not a count
        self.assertNotIn("unused", rows)                                    # a mention keyed "unused" offers both products: unresolved
        self.assertEqual(self.store.adoption({"benchmark": "unused"})["unattributed_using_papers"], 1)
        self.assertEqual(self.store.adoption({"benchmark": "unused-2"})["unattributed_using_papers"], 1)
        self.assertEqual(usage["coverage"]["unattributed_papers"], 3)       # 2401.00002 (homonym), 2501.00007 (Unused), 2501.00008 (Nobody)
        adoption = self.store.adoption({"benchmark": "fresh"})
        self.assertEqual((adoption["evaluates_on"]["others"], adoption["evaluates_on"]["self"],
                          adoption["evaluates_on"]["undetermined"], adoption["before_claim"]), (1, 1, 1, 1))
        self.assertEqual(self.store.adoption({"benchmark": "unused"})["evaluates_on"], {"others": 0, "self": 0, "undetermined": 0})
        # the cited homonym use is attributed to mirage-new (a usage row of its
        # own) but it is the introducing paper, so adoption excludes it
        self.assertEqual(rows["mirage-new"]["papers"], 1)
        self.assertEqual(self.store.adoption({"benchmark": "mirage-new"})["evaluates_on"], {"others": 0, "self": 0, "undetermined": 0})
        self.assertEqual(self.store.verify("2501.00002", "We evaluate our policies on the fresh benchmark.")["edition"], "icml-2025")
        self.assertIsNone(self.store.verify("2401.00005", "Toy | 91.2 | 88.0"))   # table cells are not quotations
        self.assertEqual(self.store.verify("2501.00001", "We introduce Fresh for evaluating robotic policies.")["edition"], "icml-2025")
        self.assertEqual(self.store.data()["registry_checked"], "2026-09-17")
        scope = self.store.scope({})
        self.assertEqual([f["name"] for f in scope["fields"]], ["robotics", "vision", "computer vision", "merged-row topic"])
        self.assertEqual(scope["label_origins"], {"declared_subarea": 9, "derived_topic": 13, "unknown": 1, "venue_track": 2,
                                                  "venue_track_mapped": 2, "venue_volume": 1})
        merged_row = next(p for p in repository.export_snapshot(self.db_path)["papers"] if p["paper_id"] == "2501.00007")
        self.assertEqual(merged_row["fields"], ["merged-row topic"])        # the topic keyed by its Oral row reached it
        by_name = {f["name"]: f for f in scope["fields"]}
        self.assertEqual(by_name["computer vision"], {"name": "computer vision", "parsed_papers": 2,
                                                      "origins": {"venue_track_mapped": 2}})
        self.assertEqual(by_name["robotics"]["origins"], {"declared_subarea": 9, "derived_topic": 10})
        self.assertNotIn("2025.acl-long", by_name)                           # kept as a label, never a field
        self.assertNotIn("mystery area", by_name)                            # unrecorded origin: kept as a label, never a field
        unknown = next(p for p in repository.export_snapshot(self.db_path)["papers"] if p["paper_id"] == "2401.00003")
        self.assertEqual((unknown["fields"], unknown["labels"]), ([], [{"label": "Mystery Area", "origin": "unknown", "field": False,
                                                                       "source": "topic of unrecorded origin"}]))
        papers = {p["paper_id"]: p for p in repository.export_snapshot(self.db_path)["papers"] if p["edition"] == "icml-2025"}
        self.assertEqual(papers["2501.00008"]["fields"], ["robotics"])                     # the derived topic only
        self.assertEqual(papers["2501.00008"]["labels"][0], {"label": "2025.acl-long", "origin": "venue_volume", "field": False,
                                                              "source": "proceedings volume id"})
        self.assertEqual([(lb["label"], lb["origin"]) for lb in papers["2501.00008"]["labels"][1:]], [("robotics", "derived_topic")])
        self.assertEqual([lb["label"] for lb in papers["2401.00005"]["labels"]],
                         ["AAAI Technical Track on Computer Vision II", "Computer Vision", "Vision"])
        self.assertEqual(papers["2401.00005"]["labels"][1]["origin"], "venue_track_mapped")
        self.assertEqual(papers["2401.00005"]["fields"], ["Computer Vision", "Vision"])
        self.assertEqual(usage["coverage"]["editions"]["icml-2025"]["label_origins"],
                         {"declared_subarea": 6, "derived_topic": 9, "venue_track": 1, "venue_track_mapped": 1, "venue_volume": 1})
        for name, args in CALLS:
            with self.subTest(tool=name, args=args):
                out = self.call(self.store, name, args)
                self.assertNotIn("ValueError", out)
                self.assertEqual(out.get("revision", self.rid), self.rid)
        for name, args in ERRORS:
            self.assertIn("ValueError", self.call(self.store, name, args))

    def test_revision_id_is_the_content(self):
        self.assertEqual(self.rid, self.built["content_sha256"][:16])
        self.assertEqual((self.first["outcome"], self.first["format"], self.first["files"]),
                         ("installed", "bench2agent-data-v2", ["benchmark_snapshot.sqlite"]))
        again = build_revision(self.root)                                  # the same inputs, built again
        self.assertEqual((again["revision_id"], again["content_sha256"]), (self.rid, self.built["content_sha256"]))
        self.assertEqual(data_module.install(self.root, file=again["path"])["outcome"], "reused")
        # one changed use is another revision; the first stays installed
        self.records[2025] = [r for r in self.records[2025] if r[0] != "2501.00006"] + [
            ("2501.00006", ["Bob Lee"], ["robotics", "Vision"], {})]
        self.write_corpus()
        changed = build_revision(self.root)
        self.assertNotEqual(changed["revision_id"], self.rid)
        installed = data_module.install(self.root, file=changed["path"])
        self.assertEqual((installed["outcome"], revisions.active_revision(self.root)), ("installed", changed["revision_id"]))
        self.assertEqual([r["id"] for r in revisions.list_revisions(self.root)], sorted([self.rid, changed["revision_id"]]))
        self.assertEqual(self.store.usage({"latest": False, "limit": 50})["snapshot_id"], self.rid)   # pinned store unmoved
        rows = {r["id"]: r["papers"] for r in BenchmarkStore(self.root).usage({"latest": False, "limit": 50})["results"]}
        self.assertEqual(rows["toy"], 3)

    def test_published_file_holds_research_tables_only_and_its_identity(self):
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta")}
        con.close()
        self.assertEqual(tables, set(repository.RESEARCH_TABLES))
        self.assertEqual((meta["revision_id"], meta["snapshot_id"], meta["content_sha256"]),
                         (self.rid, self.rid, self.built["content_sha256"]))
        self.assertEqual((meta["db_schema_version"], meta["min_client_version"]), (2, repository.MIN_CLIENT_VERSION))
        self.assertNotIn("source_sha256", meta)                                  # built directly, not imported
        self.assertNotIn("imported_at", meta)                                    # no invented dates
        self.assertEqual(self.db_path.with_name(self.db_path.name + ".sha256").read_text().split()[0], repository.file_sha256(self.db_path))
        manifest = revisions.manifest_of(self.root, self.rid)
        self.assertEqual(manifest["files"], {"benchmark_snapshot.sqlite": repository.file_sha256(self.db_path)})
        self.assertFalse(manifest["legacy_json"])
        # the logical hash ignores when and by what the content was written
        data = repository.export_snapshot(self.db_path)
        self.assertEqual(repository.content_sha256(data), self.built["content_sha256"])
        self.assertEqual(repository.content_sha256({**data, "generated_at": "later", "snapshot_id": "other", "built_with": "x"}),
                         self.built["content_sha256"])
        data["papers"][0]["uses"] = {}
        self.assertNotEqual(repository.content_sha256(data), self.built["content_sha256"])

    def test_legacy_json_import_matches_the_direct_build(self):
        data = repository.export_snapshot(self.db_path)
        data["snapshot_id"] = "legacy-1"
        legacy = self.root / "legacy.json"
        legacy.write_text(json.dumps(data))
        other = self.root / "other"
        installed = data_module.install(other, file=str(legacy))
        self.assertEqual((installed["revision"], installed["files"]), ("legacy-1", ["benchmark_snapshot.json", "benchmark_snapshot.sqlite"]))
        manifest = revisions.manifest_of(other, "legacy-1")
        self.assertTrue(manifest["legacy_json"] and manifest["sqlite_built_locally"])
        self.assertEqual(manifest["source_json_sha256"], repository.file_sha256(legacy))
        imported = BenchmarkStore(other)
        for name, args in CALLS + ERRORS:
            with self.subTest(tool=name, args=args):
                a, b = self.call(self.store, name, args), self.call(imported, name, args)
                strip = lambda d: {k: v for k, v in d.items() if k not in IDENTITY}   # noqa: E731
                self.assertEqual(json.dumps(strip(a), sort_keys=True), json.dumps(strip(b), sort_keys=True))
        self.assertEqual(imported.usage({})["snapshot_id"], "legacy-1")
        # the import refuses a snapshot it cannot read back as itself
        bad = {**data, "papers": [{**data["papers"][0], "uses": {"ghost": {"surfaces": {}, "roles": {}}}}] + data["papers"][1:]}
        broken = self.root / "broken.json"
        broken.write_text(json.dumps(bad))
        out = self.root / "broken.sqlite"
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            repository.import_snapshot(broken, out)
        self.assertFalse(out.exists())
        self.assertEqual([p.name for p in out.parent.glob(".build-*")], [])
        # the conversion utility never writes into a published revision
        with self.assertRaisesRegex(ValueError, "immutable"):
            data_module.build_sqlite(self.root, str(legacy), str(self.db_path))

    def _second_version(self, sid="second-version"):
        """Another database: one use removed, another identity."""
        data = repository.export_snapshot(self.db_path)
        data["snapshot_id"] = sid
        del next(p for p in data["papers"] if p["paper_id"] == "2501.00006")["uses"]["toy"]
        source = self.root / (sid + ".json")
        source.write_text(json.dumps(data))
        db = self.root / (sid + ".sqlite")
        repository.import_snapshot(source, db)
        return db

    def test_one_tool_call_reads_one_database_version(self):
        db2 = self._second_version()
        original = repository.SqliteRepository.observations
        swapped = []

        def observations(repo, *args, **kwargs):
            out = original(repo, *args, **kwargs)
            if not swapped:   # replace the file on disk in the middle of the call
                os.replace(db2, self.db_path)
                swapped.append(True)
            return out
        with unittest.mock.patch.object(repository.SqliteRepository, "observations", observations):
            first = self.store.usage({"latest": False, "limit": 50})
        toy = {r["id"]: r["papers"] for r in first["results"]}["toy"]
        # identity and counts both come from the version the call opened
        self.assertEqual((first["snapshot_id"], toy), (self.rid, 4))
        # a file replaced underneath a published revision is damage: the next
        # call finds the checksum wrong and refuses rather than serving it
        second = self.store.usage({"latest": False, "limit": 50})
        self.assertIn("failed verification", second["error"])

    def test_files_changed_underneath_make_the_revision_unavailable(self):
        original = self.db_path.read_bytes()
        os.replace(self._second_version(), self.db_path)
        store = BenchmarkStore(self.root)
        self.assertFalse(store.data()["available"])
        self.assertIn("does not match its recorded sha256", store.usage({})["error"])
        status = data_module.status(self.root)
        self.assertFalse(status["available"])
        self.assertIn("failed verification", status["verification"])
        self.db_path.write_bytes(original)                        # the original bytes back: served again
        self.assertTrue(BenchmarkStore(self.root).usage({})["available"])
        self.db_path.write_bytes(b"SQLite format 3\x00" + b"garbage" * 100)
        self.assertIn("failed verification", BenchmarkStore(self.root).usage({})["error"])
        self.assertFalse(data_module.status(self.root)["available"])
        self.db_path.unlink()                                     # a listed file missing is damage too
        missing = BenchmarkStore(self.root)
        self.assertFalse(missing.data()["available"])
        self.assertIn("not served until it is restored", missing.usage({})["error"])
        self.assertFalse(data_module.status(self.root)["database"]["available"])

    def test_bundle_is_database_only_and_installs_elsewhere(self):
        from bench2agent.cli import require_data
        bundle = self.root / "bundle.tar.gz"
        report = data_module.bundle(self.root, bundle)
        self.assertEqual((report["format"], report["revision"]), ("bench2agent-data-v2", self.rid))
        with tarfile.open(bundle) as archive:
            self.assertEqual(sorted(m.name for m in archive.getmembers()), ["data/processed/benchmark_snapshot.sqlite", "manifest.json"])
            manifest = json.load(archive.extractfile("manifest.json"))
        self.assertEqual((manifest["format"], manifest["revision_id"], list(manifest["files"]), manifest["min_client_version"]),
                         ("bench2agent-data-v2", self.rid, ["benchmark_snapshot.sqlite"], repository.MIN_CLIENT_VERSION))
        other = self.root / "other"
        installed = data_module.install(other, file=str(bundle), sha256=report["sha256"])
        self.assertEqual((installed["revision"], installed["files"], installed["sqlite_built_locally"]),
                         (self.rid, ["benchmark_snapshot.sqlite"], False))
        status = data_module.status(other)
        self.assertEqual((status["available"], status["format"], status["paper_observations"], status["verification"]),
                         (True, "bench2agent-data-v2", 14, "ok"))
        require_data(other, interactive=False)
        self.assertEqual(BenchmarkStore(other).usage({"topic": "robotics"})["coverage"]["parsed_papers"], 7)
        self.assertEqual(data_module.install(other, file=str(bundle))["outcome"], "reused")
        # a bundle this code is too old for is refused before anything is staged into place
        newer = self.root / "newer.tar.gz"
        with tarfile.open(bundle) as src, tarfile.open(newer, "w:gz") as dst:
            for m in src.getmembers():
                if m.name == "manifest.json":
                    payload = json.dumps({**manifest, "min_client_version": "99.0"}).encode()
                    m.size = len(payload)
                    dst.addfile(m, __import__("io").BytesIO(payload))
                else:
                    dst.addfile(m, src.extractfile(m))
        with self.assertRaisesRegex(ValueError, "99.0 or newer"):
            data_module.install(self.root / "third", file=str(newer))
        self.assertFalse((self.root / "third/data/revisions").exists() and any((self.root / "third/data/revisions").iterdir()))
        # a database whose content does not reproduce its recorded hash is refused
        forged = self.root / "forged.sqlite"
        forged.write_bytes(self.db_path.read_bytes())
        con = sqlite3.connect(forged)
        con.execute("DELETE FROM uses WHERE benchmark_id = 'toy'")
        con.execute("DELETE FROM evidence WHERE use_id NOT IN (SELECT use_id FROM uses)")
        con.execute("DELETE FROM use_roles WHERE use_id NOT IN (SELECT use_id FROM uses)")
        con.execute("DELETE FROM use_surfaces WHERE use_id NOT IN (SELECT use_id FROM uses)")
        con.commit()
        con.close()
        with self.assertRaisesRegex(ValueError, "content hash"):
            data_module.install(self.root / "fourth", file=str(forged))
        # an unsupported schema is refused with the reason
        con = sqlite3.connect(forged)
        con.execute("UPDATE meta SET value = '99' WHERE key = 'db_schema_version'")
        con.commit()
        con.close()
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            data_module.install(self.root / "fifth", file=str(forged))

    def test_legacy_v1_bundle_installs_and_a_v1_revision_keeps_serving(self):
        data = repository.export_snapshot(self.db_path)
        data["snapshot_id"] = "legacy-v1"
        bundle = self.root / "v1.tar.gz"
        v1_manifest = v1_bundle(data, self.root / "v1", bundle)
        con = sqlite3.connect(self.root / "v1/benchmark_snapshot.sqlite")
        self.assertEqual(sorted(k for (k,) in con.execute("SELECT key FROM meta")), OLD_META_KEYS)   # the real 0.4.0 layout
        con.close()
        other = self.root / "other"
        installed = data_module.install(other, file=str(bundle))
        self.assertEqual((installed["revision"], installed["format"], installed["files"], installed["sqlite_built_locally"]),
                         ("legacy-v1", "bench2agent-data-v2", ["benchmark_snapshot.json", "benchmark_snapshot.sqlite"], False))
        # the old hash was verified against the database content and kept under its rule; the new one computed
        self.assertEqual(installed["content_hashes"]["v1-json"], v1_manifest["content_sha256"])
        self.assertEqual(installed["content_hashes"]["v2-logical"], repository.content_sha256(data))
        self.assertEqual((installed["content_sha256"], installed["content_hash_rule"]), (repository.content_sha256(data), "v2-logical"))
        self.assertEqual(BenchmarkStore(other).usage({"latest": False, "limit": 50})["coverage"]["parsed_papers"], 14)
        self.assertEqual(data_module.status(other)["verification"], "ok")
        self.assertEqual(data_module.install(other, file=str(bundle))["outcome"], "reused")
        # a revision directory installed by 0.4.0 (v1 manifest) is still served, and a
        # reinstall of its bundle recognises the same content across formats, under the v1-json rule
        old = other / "data/revisions/legacy-v1/manifest.json"
        old.write_text(json.dumps({**v1_manifest, "installed_at": "2026-10-12T00:00:00+00:00"}))
        self.assertEqual(revisions.manifest_of(other, "legacy-v1")["format"], "bench2agent-data-v1")
        self.assertEqual(data_module.status(other)["verification"], "ok")
        self.assertEqual(BenchmarkStore(other).usage({})["snapshot_id"], "legacy-v1")
        self.assertEqual(data_module.install(other, file=str(bundle))["outcome"], "reused")
        self.assertEqual(json.loads(old.read_text())["format"], "bench2agent-data-v1")   # the old manifest is kept
        # re-bundling that v1 revision carries both hashes, the old one equal to what 0.4.0 recorded
        rebundle = self.root / "rebundle.tar.gz"
        report = data_module.bundle(other, rebundle)
        self.assertEqual(report["content_hashes"], {"v1-json": v1_manifest["content_sha256"], "v2-logical": repository.content_sha256(data)})
        third = self.root / "third"
        again = data_module.install(third, file=str(rebundle))
        self.assertEqual((again["files"], again["content_hashes"]), (["benchmark_snapshot.sqlite"], report["content_hashes"]))
        self.assertEqual(BenchmarkStore(third).usage({"latest": False, "limit": 50})["results"][0]["papers"],
                         BenchmarkStore(other).usage({"latest": False, "limit": 50})["results"][0]["papers"])
        # a legacy JSON kept beside the database is checked like any file
        with (other / "data/revisions/legacy-v1/benchmark_snapshot.json").open("ab") as fh:
            fh.write(b" ")
        self.assertIn("failed verification", data_module.status(other)["verification"])
        self.assertEqual(data_module.install(other, file=str(bundle))["outcome"], "restored")
        self.assertEqual(data_module.status(other)["verification"], "ok")

    def test_offered_hashes_are_verified_against_the_content_and_unknown_rules_refused(self):
        data = repository.export_snapshot(self.db_path)
        data["snapshot_id"] = "legacy-v1"
        # a v1 bundle whose manifest hash does not describe its content
        forged = self.root / "forged-v1.tar.gz"
        v1_bundle(data, self.root / "forged-v1", forged, content_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "does not reproduce the content hash offered under rule v1-json"):
            data_module.install(self.root / "a", file=str(forged))
        self.assertFalse((self.root / "a/data/revisions/legacy-v1").exists())
        # a v2 bundle whose manifest hash was altered, under either rule
        bundle = self.root / "bundle.tar.gz"
        data_module.bundle(self.root, bundle)
        with tarfile.open(bundle) as archive:
            manifest = json.load(archive.extractfile("manifest.json"))

        def rewritten(changes, name):
            out = self.root / name
            with tarfile.open(bundle) as src, tarfile.open(out, "w:gz") as dst:
                for m in src.getmembers():
                    if m.name == "manifest.json":
                        payload = json.dumps({**manifest, **changes}).encode()
                        m.size = len(payload)
                        dst.addfile(m, __import__("io").BytesIO(payload))
                    else:
                        dst.addfile(m, src.extractfile(m))
            return out
        bad_v1 = rewritten({"content_hashes": {**manifest["content_hashes"], "v1-json": "1" * 64}}, "bad-v1.tar.gz")
        with self.assertRaisesRegex(ValueError, "rule v1-json"):
            data_module.install(self.root / "b", file=str(bad_v1))
        bad_v2 = rewritten({"content_sha256": "2" * 64, "content_hashes": {**manifest["content_hashes"], "v2-logical": "2" * 64}}, "bad-v2.tar.gz")
        with self.assertRaisesRegex(ValueError, "rule v2-logical"):
            data_module.install(self.root / "b", file=str(bad_v2))
        unknown = rewritten({"content_hashes": {**manifest["content_hashes"], "v9-future": "3" * 64}}, "unknown.tar.gz")
        with self.assertRaisesRegex(ValueError, "Unknown content hash rule 'v9-future'"):
            data_module.install(self.root / "b", file=str(unknown))
        # the same rule recorded twice in one manifest must agree: a wrong single
        # field beside a correct dict entry is a contradiction, not a tie-break
        contradictory = rewritten({"content_sha256": "5" * 64}, "contradictory.tar.gz")
        with self.assertRaisesRegex(ValueError, "contradictory content hashes under rule v2-logical"):
            data_module.install(self.root / "b", file=str(contradictory))
        contradictory_v1 = rewritten({"content_sha256": manifest["content_hashes"]["v1-json"], "content_hash_rule": "v1-json",
                                      "content_hashes": {**manifest["content_hashes"], "v1-json": "6" * 64}}, "contradictory-v1.tar.gz")
        with self.assertRaisesRegex(ValueError, "contradictory content hashes under rule v1-json"):
            data_module.install(self.root / "b", file=str(contradictory_v1))
        self.assertFalse((self.root / "b/data/revisions").exists() and any((self.root / "b/data/revisions").iterdir()))
        # the same offers against the active home: nothing installed, the active revision untouched
        before = {p.name: p.read_bytes() for p in revisions.revision_dir(self.root, self.rid).iterdir()}
        for source in (forged, bad_v1, bad_v2, unknown, contradictory, contradictory_v1):
            with self.assertRaises(ValueError):
                data_module.install(self.root, file=str(source))
            self.assertEqual(revisions.active_revision(self.root), self.rid)
            self.assertEqual({p.name: p.read_bytes() for p in revisions.revision_dir(self.root, self.rid).iterdir()}, before)
            self.assertEqual([p.name for p in revisions.revisions_dir(self.root).glob(".staging-*")], [])
        self.assertTrue(data_module.status(self.root)["available"])
        # an installed manifest under an unknown rule is a verification failure, not a silent pass
        d = revisions.revision_dir(self.root, self.rid)
        original = (d / "manifest.json").read_bytes()
        (d / "manifest.json").write_text(json.dumps({**json.loads(original), "content_hashes": {"v9-future": "4" * 64}}))
        self.assertIn("Unknown content hash rule", BenchmarkStore(self.root).usage({})["error"])
        (d / "manifest.json").write_text(json.dumps({**json.loads(original), "content_sha256": "7" * 64}))   # contradicts content_hashes
        self.assertIn("contradictory content hashes", BenchmarkStore(self.root).usage({})["error"])
        self.assertIn("contradictory", data_module.status(self.root)["verification"])
        with self.assertRaisesRegex(ValueError, "contradictory"):
            data_module.bundle(self.root, self.root / "never.tar.gz")
        self.assertFalse((self.root / "never.tar.gz").exists())
        (d / "manifest.json").write_bytes(original)
        self.assertTrue(BenchmarkStore(self.root).usage({})["available"])

    def test_reuse_decision_needs_a_common_rule_or_reads_the_installed_database(self):
        bundle = self.root / "bundle.tar.gz"
        data_module.bundle(self.root, bundle)
        d = revisions.revision_dir(self.root, self.rid)
        manifest = json.loads((d / "manifest.json").read_text())
        original = (d / "manifest.json").read_bytes()
        # an installed revision with no hash at all (as a bare database installed by 0.4.0):
        # the same bytes are recognised; different bytes of the same content are read and compared
        (d / "manifest.json").write_text(json.dumps({**manifest, "content_sha256": None, "content_hashes": {}, "content_hash_rule": None}))
        self.assertEqual(data_module.install(self.root, file=str(bundle))["outcome"], "reused")
        other_bytes = self.root / "other-bytes.json"
        data = repository.export_snapshot(self.db_path)
        other_bytes.write_text(json.dumps({**data, "generated_at": "2027-01-01"}))   # same content, another export
        self.assertEqual(data_module.install(self.root, file=str(other_bytes))["outcome"], "reused")
        self.assertEqual((d / "manifest.json").read_text(), json.dumps({**manifest, "content_sha256": None, "content_hashes": {}, "content_hash_rule": None}))
        changed = {**data, "generated_at": "2027-01-01"}
        changed["papers"][0]["uses"] = {}
        other_content = self.root / "other-content.json"
        other_content.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "different content"):
            data_module.install(self.root, file=str(other_content))
        (d / "manifest.json").write_bytes(original)

    @unittest.skipUnless(PUBLIC_BUNDLE.is_file(), "public data-20261013 bundle not present locally")
    def test_public_v1_bundle_installs_serves_and_rebundles(self):
        home = self.root / "public"
        installed = data_module.install(home, file=str(PUBLIC_BUNDLE), sha256=PUBLIC_V1_SHA256)
        self.assertEqual((installed["revision"], installed["outcome"], installed["files"]),
                         ("e41da62f2fb3616f", "installed", ["benchmark_snapshot.json", "benchmark_snapshot.sqlite"]))
        with tarfile.open(PUBLIC_BUNDLE) as archive:
            shipped = json.load(archive.extractfile("manifest.json"))
        self.assertEqual(installed["content_hashes"]["v1-json"], shipped["content_sha256"])     # what 0.4.0 recorded, verified
        self.assertEqual(installed["content_hashes"]["v2-logical"][:16], "79b1b3c4d4de9d9c")  # the direct build's content id
        store = BenchmarkStore(home)
        usage = store.usage({"topic": "robotics"})
        self.assertEqual((usage["coverage"]["parsed_papers"], [(r["name"], r["papers"]) for r in usage["results"][:3]]),
                         (509, [("LIBERO", 80), ("SIMPLER", 41), ("Meta-World", 29)]))
        rebundle = self.root / "public-v2.tar.gz"
        report = data_module.bundle(home, rebundle)
        self.assertEqual(report["content_hashes"], installed["content_hashes"])
        second = self.root / "public2"
        again = data_module.install(second, file=str(rebundle), sha256=report["sha256"])
        self.assertEqual((again["revision"], again["files"], again["content_hashes"]),
                         ("e41da62f2fb3616f", ["benchmark_snapshot.sqlite"], installed["content_hashes"]))
        trend = BenchmarkStore(second).trend({"benchmark": "cifar-10", "venue": "iclr"})
        self.assertEqual([(r["edition"], r["papers"], r["parsed_papers"]) for r in trend["results"]][-3:],
                         [("iclr-2024", 178, 1888), ("iclr-2025", 197, 3038), ("iclr-2026", 165, 4237)])
        self.assertEqual(BenchmarkStore(second).usage({"topic": "robotics"})["results"][0]["papers"], 80)

    @unittest.skipUnless(RELEASE_BUNDLE.is_file(), "release bundle data-20261021 not present locally")
    def test_the_release_bundle_is_the_default_checksum_and_serves_its_pinned_figures(self):
        installed = data_module.install(self.root / "release", file=str(RELEASE_BUNDLE), sha256=data_module.DEFAULT_SHA256)
        self.assertEqual((installed["revision"], installed["outcome"], installed["files"]),
                         ("1e1fa171722da693", "installed", ["benchmark_snapshot.sqlite"]))
        self.assertIn("/data-20261021/", data_module.DEFAULT_URL)
        store = BenchmarkStore(self.root / "release")
        usage = store.usage({"topic": "robotics"})
        self.assertEqual((usage["coverage"]["parsed_papers"], [(r["name"], r["papers"]) for r in usage["results"][:3]]),
                         (513, [("LIBERO", 82), ("SIMPLER", 41), ("Meta-World", 29)]))
        self.assertEqual(usage["results"][0]["share_percent"], 16.0)

    def test_schema_1_files_are_still_read_without_labels(self):
        old = self.root / "schema1.sqlite"
        old.write_bytes(self.db_path.read_bytes())
        con = sqlite3.connect(old)
        con.execute("DROP TABLE observation_labels")
        con.execute("UPDATE meta SET value = '1' WHERE key = 'db_schema_version'")
        con.execute("DELETE FROM meta WHERE key IN ('revision_id', 'content_sha256', 'content_hash_rule', 'min_client_version')")
        con.commit()
        con.execute("VACUUM")
        con.close()
        other = self.root / "other"
        installed = data_module.install(other, file=str(old))
        self.assertEqual(installed["db_schema_version"], 1)
        store = BenchmarkStore(other)
        usage = store.usage({"latest": False, "limit": 50})
        self.assertEqual({r["id"]: r["papers"] for r in usage["results"]}["toy"], 4)
        scope = store.scope({})
        self.assertEqual(scope["label_origins"], {})                          # an older file records no origins
        self.assertNotIn("origins", scope["fields"][0])
        exported = repository.export_snapshot(other / "data/revisions" / installed["revision"] / "benchmark_snapshot.sqlite")
        self.assertNotIn("labels", exported["papers"][0])
        self.assertEqual(repository.export_snapshot(self.db_path)["papers"][0]["fields"], exported["papers"][0]["fields"])

    def test_status_reports_the_database(self):
        status = data_module.status(self.root)
        self.assertEqual((status["available"], status["revision"], status["format"], status["files"], status["verification"]),
                         (True, self.rid, "bench2agent-data-v2", ["benchmark_snapshot.sqlite"], "ok"))
        self.assertEqual((status["database"]["paper_observations"], status["database"]["revision_id"]), (14, self.rid))
        self.assertEqual(status["paper_observations"], 14)
        self.assertEqual([(r["id"], r["active"], r["format"]) for r in status["revisions"]], [(self.rid, True, "bench2agent-data-v2")])
        self.assertNotIn("requested_backend", status)


if __name__ == "__main__":
    unittest.main()
