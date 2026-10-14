"""The browser's union is built on one normalization; a later normalization
may merge a union row into a paper whose representative row is not in the
union. Every lookup must still reach the paper: card by old id, fallback
search, result selection, evidence (paper detail)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bench2agent.core import mcp


class BrowserLookupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        processed = root / "data/processed"
        processed.mkdir(parents=True)
        # union built when 130310 (an Oral row) represented the paper; the paper
        # now merges under 118806 with 130310 as a duplicate row
        (processed / "union.json").write_text(json.dumps({
            "note": "", "order": "sorted", "corpora": ["neurips-2025"],
            "keys": ["neurips-2025", "neurips-2025"], "eids": [130310, 200]}))
        (processed / "papers_neurips_2025.jsonl").write_text("\n".join(json.dumps(r) for r in [
            {"event_id": 118806, "title": "Perception Encoder: the best visual embeddings", "titles": ["Perception Encoder: the best visual embeddings"],
             "abstract": "We study visual embeddings.", "authors": ["A B"], "duplicate_event_ids": [130310], "area": "Computer Vision"},
            {"event_id": 200, "title": "Another paper", "abstract": "Nothing about embeddings.", "authors": ["C D"]}]))
        (root / "reports/data").mkdir(parents=True)
        (root / "reports/data/spans_neurips-2025.json").write_text("{}")
        (processed / "citations.json").write_text(json.dumps({"edges": [], "papers_with_refs": 0}))
        # parsed full text keyed by arXiv id, bridged to the paper's CURRENT representative row (118806)
        interim, arxraw = root / "data/interim", root / "data/raw/arxiv"
        interim.mkdir(); arxraw.mkdir(parents=True)
        (arxraw / "resolved_neurips-2025.jsonl").write_text(json.dumps({"event_id": 118806, "arxiv_base": "2504.13181"}) + "\n")
        (interim / "fulltext_neurips_2025.jsonl").write_text(json.dumps({
            "arxiv_base": "2504.13181", "ok": True, "sections": [
                {"bucket": "intro", "title": "Introduction", "text": "We present Perception Encoder."},
                {"bucket": "experiments", "title": "Experiments", "text": "We evaluate our embeddings on ImageNet-1k and COCO."}]}) + "\n")
        self.patches = [patch.object(mcp, "PROCESSED", processed), patch.object(mcp, "RDATA", root / "reports/data"),
                        patch.object(mcp, "INTERIM", interim), patch.object(mcp, "ARXRAW", arxraw),
                        patch.object(mcp, "S", mcp.Store()), patch.object(mcp.Store, "meili", lambda self, path, body: None)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_old_union_id_reaches_the_merged_paper(self):
        self.assertEqual(mcp.S.rec(0)["event_id"], 118806)
        self.assertEqual(mcp.S.brief(0)["title"], "Perception Encoder: the best visual embeddings")

    def test_fallback_search_walks_the_union_and_the_result_opens(self):
        res = mcp.t_search({"query": "visual embeddings"})
        self.assertEqual([r["gid"] for r in res["results"]], [0])
        self.assertIn("fallback", res["engine"])
        self.assertEqual(mcp.t_search({"query": "embeddings"})["results"][0]["gid"], 0)
        self.assertEqual([r["gid"] for r in mcp.t_search({"query": "nothing"})["results"]], [1])
        detail = mcp.t_paper({"gid": 0})
        self.assertEqual(detail.get("title") or detail.get("paper", {}).get("title"), "Perception Encoder: the best visual embeddings")

    def test_full_text_and_quote_verification_reach_the_paper_through_its_old_id(self):
        sections = mcp.t_paper_text({"gid": 0})
        self.assertEqual([x["bucket"] for x in sections["sections"]], ["intro", "experiments"])
        text = mcp.t_paper_text({"gid": 0, "section": "experiments"})
        self.assertIn("ImageNet-1k and COCO", text["text"])
        verified = mcp.t_verify_quote({"gid": 0, "quote": "We evaluate our embeddings on ImageNet-1k and COCO."})
        self.assertTrue(verified["verified"], verified)
        self.assertFalse(mcp.t_verify_quote({"gid": 0, "quote": "This sentence is not in the paper at all."})["verified"])
        self.assertIn("error", mcp.t_paper_text({"gid": 1}))               # a paper with no parsed text says so


if __name__ == "__main__":
    unittest.main()
