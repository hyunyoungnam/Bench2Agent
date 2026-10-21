"""Links an introducing paper prints that were not confirmed as a benchmark's location (config/introduced_paper_links.json):
kept apart from locations, under the paper that printed them, dated observations only, quotable as that paper's text."""
import gzip
import hashlib
import json
import unittest

from bench2agent.core import policy
from bench2agent.core.benchmarks import LINK_NOTE, build_revision, introducing_paper, other_paper_links

import test_benchmark_chat as base

LINE = "Code — https://github.com/lab/fresh-code"
PAGE = b"<html><p id='p2.1'>Code \xe2\x80\x94 https://github.com/lab/fresh-code</p></html>"


def link(paper_id="2501.00001", url="https://github.com/lab/fresh-code", line=LINE, sha=None, reviews=None):
    return {"paper_id": paper_id, "label": "Code", "line": line, "url": url, "host": "github",
            "source": {"archive_sha256": sha or hashlib.sha256(PAGE).hexdigest(), "arxiv_version": "v1", "element_id": "p2.1"},
            "observed": {"checked_at": "2026-10-21T00:00:00Z", "method": "github api", "reached": True,
                         "summary": "repository reached on 2026-10-21 UTC; file list of branch main read: 1 file; files: README.md; README read"},
            "reviews": reviews if reviews is not None else [
                {"benchmark_id": "fresh", "not_confirmed_because": "the benchmark's name was not found in the text read there"}]}


class PaperLinkServing(base.BenchmarkQuestions):
    """Runs on the representative fixture: Fresh and Unused are both introduced by 2501.00001."""

    def rebuild(self, links, locations=()):
        self.write("config/introduced_paper_links.json", {"links": links})
        self.write("config/introduced_locations.json", {"locations": list(locations)})
        result = build_revision(self.root)
        self.publish()
        return result

    def test_a_paper_link_sits_under_its_paper_never_among_the_locations(self):
        same_url = {"benchmark_id": "unused", "url": "https://github.com/lab/fresh-code", "host": "github", "artifact": "code_repository",
                    "verification": "AI individual review", "data_status": "not verified", "basis": "the introducing paper links it",
                    "paper_id": "2501.00001", "release_sentence": LINE}
        built = self.rebuild([link()], [same_url])
        self.assertEqual(built["paper_links"], 1)                                       # Unused has it as a location already
        fresh = self.store.adoption({"benchmark": "fresh"})
        self.assertEqual(fresh["locations"], [])                                        # still no confirmed location
        [shown] = fresh["introducing_paper"]["links"]
        self.assertEqual((shown["paper_id"], shown["line"], shown["url"]), ("2501.00001", LINE, "https://github.com/lab/fresh-code"))
        self.assertIn("2026-10-21 UTC", shown["observed"])
        self.assertIn("not found", shown["not_confirmed_because"])
        self.assertEqual(shown["source"], {"arxiv_version": "v1", "element_id": "p2.1"})
        self.assertNotIn("other_paper_links", fresh)
        rows = {r["id"]: r for r in self.store.new({})["results"]}
        self.assertIn("links", rows["fresh"]["introducing_paper"])
        self.assertNotIn("links", rows["unused"]["introducing_paper"])                 # never duplicated beside its location
        self.assertEqual([l["check"] for l in rows["unused"]["locations"]], ["reviewed"])
        repo = self.store.repo()
        with repo.session():
            self.assertIsNotNone(repo.verify_quote("2501.00001", LINE))                 # the paper's own line
            self.assertIsNone(repo.verify_quote("2501.00005", LINE))                    # not another paper's
            self.assertIsNone(repo.verify_quote("2501.00001", "Code — https://github.com/lab/other"))

    def test_a_changed_archive_leaves_the_link_out_until_reviewed_again(self):
        (self.root / "data/raw/html").mkdir(parents=True, exist_ok=True)
        (self.root / "data/raw/html/2501.00001.html.gz").write_bytes(gzip.compress(PAGE))
        self.assertEqual(self.rebuild([link()])["paper_links"], 2)                      # the page it was read from: Fresh and Unused
        built = self.rebuild([link(sha="0" * 64)])
        self.assertEqual((built["paper_links"], built["paper_links_left_out_changed_archive"]),
                         (0, [{"paper_id": "2501.00001", "url": "https://github.com/lab/fresh-code"}]))
        self.assertNotIn("links", self.store.adoption({"benchmark": "fresh"})["introducing_paper"])

    def test_a_revision_without_paper_links_serves_as_before(self):
        self.assertEqual(self.rebuild([])["paper_links"], 0)
        fresh = self.store.adoption({"benchmark": "fresh"})
        self.assertEqual(set(fresh["introducing_paper"]), {"paper_id", "url", "edition", "title"})
        self.assertNotIn("other_paper_links", fresh)



for name in [n for n in dir(base.BenchmarkQuestions) if n.startswith("test_")]:
    setattr(PaperLinkServing, name, None)                                               # inherit the fixture, not its tests


class TwoIntroducingPapers(unittest.TestCase):
    def test_each_link_stays_with_the_paper_that_printed_it(self):
        entry = {"id": "x", "first_claim": "icml-2025",
                 "claims": [{"arxiv_base": "2501.00001", "edition": "icml-2025", "title": "First"},
                            {"arxiv_base": "2601.00009", "edition": "icml-2026", "title": "Second"}],
                 "paper_links": [{**link(), "not_confirmed_because": "a"},
                                 {**link("2601.00009", "https://github.com/other/second", "Data — https://github.com/other/second"),
                                  "not_confirmed_because": "b"}]}
        shown = introducing_paper(entry)
        self.assertEqual(shown["paper_id"], "2501.00001")
        self.assertEqual([l["paper_id"] for l in shown["links"]], ["2501.00001"])       # only that paper's own links under it
        self.assertEqual([(l["paper_id"], l["url"]) for l in other_paper_links(entry)], [("2601.00009", "https://github.com/other/second")])


class Wording(unittest.TestCase):
    def test_the_tools_and_the_policy_say_what_a_paper_link_is_and_is_not(self):
        self.assertIn("introducing_paper.links", LINK_NOTE)
        self.assertIn("NOT confirmed", LINK_NOTE)
        self.assertIn("not a finding that it was deleted", LINK_NOTE)
        self.assertIn("introducing_paper.links", policy.BENCHMARK_SYSTEM)
        self.assertIn('never turn it into "the data does not exist"', policy.BENCHMARK_SYSTEM)
        self.assertIn("Empty locations still means no confirmed location", policy.BENCHMARK_SYSTEM)


if __name__ == "__main__":
    unittest.main()
