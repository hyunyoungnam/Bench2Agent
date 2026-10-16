"""The OpenAlex/Tavily pilot's offline parts: URL kinds, official ids, caps, cache, key handling. No network."""
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from icml import enrich_pilot as ep


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / ".env").write_text("UV_PUBLISH_TOKEN=pypi-secret\nOPENALEX_API_KEY=oa-secret-123\nTAVILY_API_KEY=\n")
        pilot = root / "data/work/pilot"
        for name, value in (("ROOT", root), ("PILOT", pilot), ("CACHE", pilot / "cache.jsonl"), ("LEDGER", pilot / "ledger.jsonl")):
            p = unittest.mock.patch.object(ep, name, value); p.start(); self.addCleanup(p.stop)

    def test_url_kinds(self):
        self.assertEqual(ep.classify_url("https://github.com/allenai/WildBench/tree/main/scripts"), ("gh", "allenai/WildBench"))
        self.assertEqual(ep.classify_url("huggingface.co/datasets/MMMU/MMMU"), ("hfd", "MMMU/MMMU"))
        self.assertEqual(ep.classify_url("https://huggingface.co/papers/2401.00001")[0], "other")
        self.assertEqual(ep.classify_url("https://groundmore.github.io/")[0], "project")
        self.assertEqual(ep.classify_url("https://arxiv.org/abs/2401.00001")[0], "paper")

    def test_anthology_doi_follows_the_anthology_rule_only(self):
        self.assertEqual(ep.anthology_doi({"paper_url": "https://aclanthology.org/2023.acl-long.1"}), "10.18653/v1/2023.acl-long.1")
        self.assertIsNone(ep.anthology_doi({"paper_url": "https://openaccess.thecvf.com/x.html"}))

    def test_only_the_two_named_keys_are_read_and_an_empty_key_stops_the_service(self):
        self.assertEqual(ep._key("OPENALEX_API_KEY"), "oa-secret-123")
        self.assertIsNone(ep._key("TAVILY_API_KEY"))
        self.assertEqual(ep._scrub("error at oa-secret-123"), "error at ***")
        with unittest.mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            with self.assertRaises(ep.Stop):
                ep.request("tavily_search", "search", {"query": "x"})

    def test_cache_hits_are_free_and_the_cap_stops_before_any_request(self):
        payload = {"filter": "doi:10.1/x"}
        ep._append(ep.CACHE, {"k": ep._cache_key("openalex", "works", payload), "service": "openalex", "response": {"results": [1]}})
        with unittest.mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            self.assertEqual(ep.request("openalex", "works", payload), {"results": [1]})
            for _ in range(ep.CAPS["openalex"]):
                ep._append(ep.LEDGER, {"service": "openalex", "event": "sent"})
            with self.assertRaises(ep.Stop):
                ep.request("openalex", "works", {"filter": "doi:10.1/y"})
        rows = [json.loads(l) for l in ep.LEDGER.read_text().splitlines()]
        self.assertEqual(sum(r["event"] == "cache_hit" for r in rows), 1)
        self.assertNotIn("oa-secret-123", ep.LEDGER.read_text() + ep.CACHE.read_text())

    def test_authors_pair_one_to_one_and_initials_are_counted_apart(self):
        # Codex's case: one "A. Smith" cannot stand for two Smiths
        self.assertEqual(ep.author_match(["Alice Smith", "Andrew Smith"], ["A. Smith"]), {"conference": 2, "candidate": 1, "full": 0, "initial_only": 1})
        self.assertEqual(ep.author_match(["Tin Hadži Veljković", "A. Smith"], ["Tin Hadzi Veljkovic", "Alice Smith"]),
                         {"conference": 2, "candidate": 2, "full": 1, "initial_only": 1})
        self.assertEqual(ep.author_match(["Oran Lang"], ["Chase van de Geijn"])["full"], 0)


if __name__ == "__main__":
    unittest.main()
