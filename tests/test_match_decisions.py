"""Reviewed match decisions: applied only while the reviewed inputs hold, never past the collision check."""
import json
import tempfile
import unittest
from pathlib import Path

from icml.arxiv_match import apply_decisions, refuse_collisions


def paper(eid, title, authors, sids=("s",)):
    return {"event_id": eid, "title": title, "authors": list(authors), "source_ids": list(sids)}


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.papers = [paper(1, "ReMix: Optimizing Data Mixtures", ["Joey Hejna", "Chethan Bhateja"]),
                       paper(2, "Another Paper", ["Ann Other"])]
        self.results = [{"event_id": 1, "title": self.papers[0]["title"], "match": "none"},
                        {"event_id": 2, "title": "Another Paper", "match": "none"}]
        self.index = {"2408.14037": {"arxiv_base": "2408.14037", "title": "Re-Mix: Optimizing Data Mixtures", "date": "2024-08-26"}}
        self.d = {"id": "md-1", "edition": "corl-2024", "event_id": 1, "arxiv_base": "2408.14037",
                  "inputs": {"title": self.papers[0]["title"], "authors": list(self.papers[0]["authors"]), "source_ids": ["s"],
                             "arxiv_title": "Re-Mix: Optimizing Data Mixtures"}}

    def test_applied_while_the_reviewed_inputs_hold(self):
        c = apply_decisions(self.results, self.papers, self.index, [self.d])
        self.assertEqual(c["applied"], 1)
        self.assertEqual((self.results[0]["arxiv_base"], self.results[0]["match"], self.results[0]["decision"]["status"]), ("2408.14037", "reviewed", "applied"))

    def test_a_changed_paper_or_arxiv_record_makes_the_decision_stale(self):
        self.papers[0]["authors"].append("New Author")
        c = apply_decisions(self.results, self.papers, self.index, [self.d])
        self.assertEqual((c["stale"], self.results[0].get("arxiv_base")), (1, None))
        self.papers[0]["authors"].pop()
        self.index["2408.14037"]["title"] = "Re-Mix v2: something else"
        c = apply_decisions(self.results, self.papers, self.index, [self.d])
        self.assertEqual(c["stale"], 1)
        self.assertIn("arXiv record's title", self.results[0]["decision"]["status"])

    def test_existing_links_are_conflicts_not_overwritten(self):
        self.results[0].update({"arxiv_base": "2401.00001", "match": "fuzzy"})
        c = apply_decisions(self.results, self.papers, self.index, [self.d])
        self.assertEqual((c["conflict"], self.results[0]["arxiv_base"]), (1, "2401.00001"))
        self.results[0].pop("arxiv_base"); self.results[0]["match"] = "none"
        self.results[1].update({"arxiv_base": "2408.14037", "match": "exact"})          # the record belongs to another paper
        c = apply_decisions(self.results, self.papers, self.index, [self.d])
        self.assertEqual((c["conflict"], self.results[0].get("arxiv_base")), (1, None))

    def test_two_decisions_for_one_record_apply_once_and_the_collision_check_still_runs(self):
        d2 = {**self.d, "id": "md-2", "event_id": 2, "inputs": {**self.d["inputs"], "title": "Another Paper", "authors": ["Ann Other"]}}
        c = apply_decisions(self.results, self.papers, self.index, [self.d, d2])
        self.assertEqual((c["applied"], c["conflict"]), (1, 1))
        self.results[1].update({"arxiv_base": "2408.14037"})                             # forced, as a title rule might
        self.assertEqual(refuse_collisions(self.results, self.papers), 2)               # disjoint authors: neither keeps it
        self.assertEqual({r["match"] for r in self.results}, {"collision"})


class LocationTests(unittest.TestCase):
    def test_a_reviewed_location_reaches_the_tool_row_by_benchmark_id(self):
        from bench2agent.core.benchmarks import catalogue, locations
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "config").mkdir()
            (root / "config/benchmarks.json").write_text(json.dumps({"benchmarks": []}))
            (root / "config/benchmarks_introduced.json").write_text(json.dumps({"benchmarks": [
                {"id": "eidseg", "name": "EIDSeg", "kind": "benchmark", "developers": ["Huili Huang"],
                 "claims": [{"claim_id": "2511.06456#eidseg", "arxiv_base": "2511.06456", "edition": "aaai-2026", "evidence": "x"}]},
                {"id": "eidseg-2", "name": "EIDSeg", "kind": "benchmark", "developers": ["Other Person"],
                 "claims": [{"claim_id": "2601.00001#eidseg", "arxiv_base": "2601.00001", "edition": "cvpr-2026", "evidence": "y"}]}]}))
            (root / "config/introduced_locations.json").write_text(json.dumps({"locations": [
                {"benchmark_id": "eidseg", "url": "https://huggingface.co/datasets/huilihuang413/EIDSeg", "host": "huggingface",
                 "artifact": "data_repository", "verification": "AI individual review", "data_status": "dataset page checked",
                 "basis": "the introducing paper links it", "checked_at": "2026-10-16", "reviewed_by": "Claude Code (AI review)"}]}))
            cat = {e["id"]: e for e in catalogue(root)}
            row = locations(cat["eidseg"])
            self.assertEqual([(l["check"], l["artifact"], l["verification"], l["data_status"]) for l in row],
                             [("reviewed", "data_repository", "AI individual review", "dataset page checked")])
            self.assertEqual(locations(cat["eidseg-2"]), [])                                # the homonym keeps its own (none)

    def test_a_revision_built_with_the_earlier_location_schema_still_serves(self):
        from bench2agent.core.benchmarks import locations
        old = {"reviewed_locations": [{"url": "https://github.com/oferidan1/LaVPR", "host": "github", "distribution": "official_code_repository",
                                       "data_available": "not verified", "basis": "the introducing paper links it", "checked_at": "2026-10-16"}]}
        row = locations(old)[0]
        self.assertEqual((row["artifact"], row["data_status"], row["check"]), ("code_repository", "not verified", "reviewed"))

    def test_a_release_sentence_is_quotable_with_its_paper_and_nothing_else_is(self):
        from bench2agent.core.repository import SOURCE, build_database, SqliteRepository
        sentence = "Our dataset and code are available at https://github.com/oferidan1/LaVPR."
        entry = {"id": "lavpr", "name": "LaVPR", "kind": "benchmark", "developers": [], "claims": [
                    {"claim_id": "2602.03253#lavpr", "arxiv_base": "2602.03253", "edition": "eccv-2026", "title": "LaVPR", "noun": "benchmark",
                     "evidence": "We introduce LaVPR, a benchmark.", "label": "E"}],
                 "reviewed_locations": [{"url": "https://github.com/oferidan1/LaVPR", "host": "github", "artifact": "code_repository",
                                         "verification": "AI individual review", "data_status": "not verified", "basis": "the introducing paper links it",
                                         "paper_id": "2602.03253", "release_sentence": sentence}]}
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "x.sqlite"
            build_database({"schema_version": 1, "source": SOURCE, "snapshot_id": "t", "generated_at": "2026-10-16", "benchmarks": [entry], "editions": {}, "papers": []}, db)
            repo = SqliteRepository(db)
            with repo.session():
                self.assertIsNotNone(repo.verify_quote("2602.03253", "Our dataset and code are available at https://github.com/oferidan1/LaVPR."))
                self.assertIsNone(repo.verify_quote("2602.03253", "Our dataset and code are available at https://github.com/someone-else/LaVPR."))
                self.assertIsNone(repo.verify_quote("2401.00001", "Our dataset and code are available at https://github.com/oferidan1/LaVPR."))


if __name__ == "__main__":
    unittest.main()
