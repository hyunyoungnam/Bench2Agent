"""Paper links: host-qualified ids, existence labels, short-name matching, the v3 decision order. No network."""
import unittest
import unittest.mock

from icml import paper_links as pl


class PaperLinkTests(unittest.TestCase):
    def test_a_hub_401_is_not_existence(self):
        with unittest.mock.patch.object(pl, "_get", return_value=(401, {}, b"", None)):
            self.assertEqual(pl.check_hub("hfd", "THUIR/MemoryBenchand")["existence"], "unknown: 401 (private, gated or missing)")
        with unittest.mock.patch.object(pl, "_get", return_value=(None, {}, b"", "URLError")):
            self.assertEqual(pl.check_hub("hfd", "x/y")["existence"], "unchecked")              # never "gone"

    def test_github_limits_and_auth_stop_and_are_never_gone(self):
        with unittest.mock.patch.object(pl, "_get", return_value=(401, {}, b"", None)):
            with self.assertRaises(pl.Halt):
                pl.check_github("a/b", "t")
        with unittest.mock.patch.object(pl, "_get", return_value=(403, {"X-RateLimit-Remaining": "0"}, b"rate limit", None)):
            with self.assertRaises(pl.Halt):
                pl.check_github("a/b", "t")
        with unittest.mock.patch.object(pl, "_get", return_value=(200, {"X-RateLimit-Remaining": "4999"}, b"{not json", None)):
            self.assertEqual(pl.check_github("a/b", "t")["existence"], "exists")                # a 200 is the existence fact

    def test_short_names_need_an_exact_case_word(self):
        self.assertTrue(pl.names_in("python benchmark.py --type NFS --json_file x", ["NFS"]))
        self.assertFalse(pl.names_in("configs for transformers", ["NFS"]))
        self.assertTrue(pl.names_in("# MM-SAP\nMM-SAP is a benchmark", ["MM-SAP"]))

    def test_v3_decides_in_order(self):
        cand = {"name": "ToyBench", "kind": "gh"}
        exists = {"existence": "exists"}
        self.assertEqual(pl.decide_v3(cand, None, exists, "ToyBench", [])[0], "excluded")
        self.assertEqual(pl.decide_v3(cand, {"tags": []}, {"existence": "gone"}, "ToyBench", [])[0], "unconfirmed")
        self.assertEqual(pl.decide_v3(cand, {"tags": []}, exists, "a repository about something else", [])[0], "unconfirmed")
        self.assertEqual(pl.decide_v3(cand, {"tags": ["related_page"]}, exists, "ToyBench leaderboard", [])[0], "related_artifact")
        self.assertEqual(pl.decide_v3(cand, {"tags": ["related_page"]}, exists, "ToyBench data: huggingface.co/datasets/a/b", [])[0], "candidate:code_repository")
        self.assertEqual(pl.decide_v3({**cand, "kind": "hfd"}, {"tags": []}, exists, "# ToyBench card", [])[0], "candidate:data_repository")
        self.assertEqual(pl.decide_v3({**cand, "kind": "hfm"}, {"tags": []}, exists, "ToyBench model", [])[0], "related_artifact")


class NameIsNotRelationTests(unittest.TestCase):
    def test_a_readme_that_only_mentions_the_benchmark_never_confirms_it(self):
        # 2026-10-19: EHRXQA's README names MIMIC-CXR-VQA once, as the component it was built from;
        # the dedicated repository is baeseongsu/mimic-cxr-vqa. The rule may propose, never confirm.
        readme = ("# EHRXQA: A Multi-Modal Question Answering Dataset for Electronic Health Records with Chest X-ray Images\n"
                  "To develop our dataset, we first construct two uni-modal resources: 1) The MIMIC-CXR-VQA dataset, our newly "
                  "created medical visual question answering (VQA) benchmark ... ## Reproducing the Dataset")
        d, why = pl.decide_v3({"name": "MIMIC-CXR-VQA", "kind": "gh"}, {"tags": ["named", "multi_intro_paper"]}, {"existence": "exists"}, readme, [])
        self.assertTrue(d.startswith("candidate:"), d)
        self.assertNotIn(d, pl.CONFIRMING)
        self.assertEqual(pl.CONFIRMING, frozenset())


if __name__ == "__main__":
    unittest.main()
