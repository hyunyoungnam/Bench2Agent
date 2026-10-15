"""The incremental update: a working database of stage status, re-run scope
decided by what changed, retryable parse failures, a review queue for new
and changed claims, one run at a time, and a due check for a timer."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

from icml import update


class FakePipeline(update.Pipeline):
    """Stages that write the files the real ones would, without the network."""

    def __init__(self, work, scenario):
        super().__init__(work, parser_python=sys.executable)
        self.s = scenario
        self.calls = []

    def harvest(self, since, until):
        self.calls.append(("harvest", since, until))
        return "done — fake harvest"

    def collect(self, paths):
        self.calls.append(("collect", paths["key"]))

    def index_dates(self):
        return dict(self.s.get("dates", {}))

    def index_state(self):
        import hashlib
        stamps = dict(self.s.get("dates", {}))
        return {"stamps": stamps, "hash": hashlib.sha256(json.dumps(sorted(stamps.items())).encode()).hexdigest()[:16]}

    def _text(self, base):
        version = self.s.get("text_version", {}).get(base, "v1")
        return f"We evaluate on Toy ({base}, {version})."

    def _row(self, base):
        import hashlib
        text = self._text(base)
        return {"arxiv_base": base, "ok": True, "sections": [{"bucket": "experiments", "text": text}],
                "html_sha256": hashlib.sha256(text.encode()).hexdigest()}

    def recheck(self, paths, bases):
        self.calls.append(("recheck", paths["key"], tuple(bases)))
        rows, transient = [], []
        scratch = self.work.root / "data/work/recheck/archive"
        scratch.mkdir(parents=True, exist_ok=True)
        for base in bases:
            if self.s.get("recheck_fails", {}).get(base):
                transient.append((base, "HTTP 503"))
                continue
            row = self._row(base)
            rows.append(row)
            import gzip
            (scratch / f"{base}.html.gz").write_bytes(gzip.compress(self._text(base).encode()))
        return rows, transient

    def normalize(self, paths):
        self.calls.append(("normalize", paths["key"]))
        paths["papers"].parent.mkdir(parents=True, exist_ok=True)
        paths["papers"].write_text("\n".join(json.dumps({"event_id": e, "title": f"Paper {e}"}) for e in self.s["papers"][paths["key"]]) + "\n")

    def match(self, paths):
        self.calls.append(("match", paths["key"]))
        paths["resolved"].parent.mkdir(parents=True, exist_ok=True)
        paths["resolved"].write_text("\n".join(json.dumps({"event_id": e, "arxiv_base": f"25{e:02d}.00001"})
                                                for e in self.s["papers"][paths["key"]]) + "\n")

    def parse(self, paths, *, reparse, limit=0):
        self.calls.append(("reparse" if reparse else "parse", paths["key"]))
        ft = paths["fulltext"]
        ft.parent.mkdir(parents=True, exist_ok=True)
        done = set()
        rows = []
        if ft.exists() and not reparse:
            for line in ft.read_text().splitlines():
                r = json.loads(line)
                if r.get("ok") or not r.get("transient"):
                    rows.append(r)
                    done.add(r["arxiv_base"])
        transient = []
        for line in paths["resolved"].read_text().splitlines():
            base = json.loads(line)["arxiv_base"]
            if base in done:
                continue
            fail = self.s.get("fail", {}).get(base)
            if fail == "transient" and self.s.get("network_down"):
                transient.append((base, "HTTP 429"))                      # like html_extract: reported, not recorded
            elif fail == "permanent":
                rows.append({"arxiv_base": base, "ok": False, "error": "no-html"})
            else:
                row = self._row(base)
                rows.append(row)
                paths["archive"].mkdir(parents=True, exist_ok=True)
                import gzip
                (paths["archive"] / f"{base}.html.gz").write_bytes(gzip.compress(self._text(base).encode()))
        ft.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return f"done — {len(rows)} parsed, 0 unavailable/thin, {len(transient)} transient (not recorded)", transient

    def mentions(self, paths):
        self.calls.append(("mentions", paths["key"]))
        paths["mentions"].parent.mkdir(parents=True, exist_ok=True)
        rows = [json.loads(l) for l in paths["fulltext"].read_text().splitlines()]
        paths["mentions"].write_text("\n".join(json.dumps({"arxiv_base": r["arxiv_base"], "mentions": {}}) for r in rows if r.get("ok")) + "\n")

    def claims(self):
        self.calls.append(("claims",))
        return {k: dict(v) for k, v in self.s["claims"].items()}

    def reviewed(self):
        return {k: dict(v) for k, v in self.s.get("reviewed", {}).items()}

    def retain_vanished(self, cid, record):
        self.calls.append(("retain", cid))
        self.s.setdefault("reviewed", {}).setdefault(cid, {})["retained_evidence"] = {k: record.get(k) for k in ("edition", "evidence", "evidence_sha", "reason")}

    def build(self, publish):
        self.calls.append(("build", publish))
        return {"revision_id": "rev-" + str(len(self.calls)), "parsed_papers": 0, "editions": 2, "benchmarks": 1,
                **({"published": {"outcome": "installed"}} if publish else {})}


class UpdateTests(unittest.TestCase):
    EDS = [("ICLR", 2025), ("ICLR", 2026)]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "src/icml").mkdir(parents=True)
        (self.root / "config").mkdir()
        for name in ("normalize.py", "arxiv_match.py", "html_extract.py", "bench_mentions.py", "taxonomy.py"):
            (self.root / "src/icml" / name).write_text(f"# {name} v1\n")
        (self.root / "config/benchmarks.json").write_text('{"benchmarks": []}')
        self.work = update.Work(self.root)
        self.addCleanup(self.work.close)
        self.scenario = {"papers": {"iclr-2025": [1, 2, 3], "iclr-2026": [4, 5]},
                         "claims": {"2501.00001#toy": {"edition": "iclr-2025", "sha": "sha-a", "evidence": "We introduce Toy.", "name": "Toy", "noun": "benchmark", "arxiv_base": "2501.00001", "title": "Paper 1"},
                                    "2504.00001#fresh": {"edition": "iclr-2026", "sha": "sha-b", "evidence": "We introduce Fresh.", "name": "Fresh", "noun": "benchmark", "arxiv_base": "2504.00001", "title": "Paper 4"}},
                         "reviewed": {"2501.00001#toy": {"label": "E", "status": "confirmed", "evidence_sha": "sha-a"}}}
        self.pipe = FakePipeline(self.work, self.scenario)

    def bump(self, name):
        p = self.root / "src/icml" / name
        p.write_text(p.read_text() + "# changed\n")

    def test_first_run_takes_every_edition_through_every_stage_and_queues_new_claims(self):
        dry = update.run(self.work, self.pipe, editions=self.EDS, dry_run=True, discover=False)
        self.assertEqual(dry["editions"]["iclr-2025"]["need"], ["normalize", "match"])
        self.assertEqual(self.pipe.calls, [])                                  # a dry run runs nothing
        out = update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual(out["editions"]["iclr-2025"]["did"], ["normalize", "match", "parse", "mentions"])
        self.assertEqual((out["editions"]["iclr-2025"]["parsed_ok"], out["editions"]["iclr-2025"]["mentions"]), (3, 3))
        self.assertEqual({k: out["claims"][k] for k in ("total", "queued_new", "queued_changed")}, {"total": 2, "queued_new": 1, "queued_changed": 0})   # the reviewed one is not queued
        self.assertEqual(out["build"]["revision_id"], "rev-" + str(len(self.pipe.calls)))
        rows = self.work.con.execute("SELECT edition, event_id, status FROM paper_stages ORDER BY edition, event_id").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("iclr-2025", "1", "ok"), ("iclr-2025", "2", "ok"), ("iclr-2025", "3", "ok"),
                                                   ("iclr-2026", "4", "ok"), ("iclr-2026", "5", "ok")])
        self.assertEqual([tuple(r) for r in self.work.con.execute("SELECT claim_id, reason FROM claim_queue")], [("2504.00001#fresh", "new")])
        self.assertIsNotNone(self.work.last_success())
        # nothing changed: the next run re-runs no stage, builds again
        before = list(self.pipe.calls)
        again = update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual([c for c in self.pipe.calls[len(before):] if c[0] != "build"], [])
        self.assertEqual(again["claims"], {})                                 # nothing touched: claims not recomputed

    def test_transient_failures_are_retried_and_permanent_ones_are_kept(self):
        self.scenario["fail"] = {"2502.00001": "transient", "2503.00001": "permanent"}
        self.scenario["network_down"] = True
        out = update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        f = out["editions"]["iclr-2025"]
        self.assertEqual((f["parsed_ok"], f["parsed_failed"], f["transient_now"]), (1, 1, 1))
        self.assertEqual(f["parse_report"], "done — 2 parsed, 0 unavailable/thin, 1 transient (not recorded)")   # the final line
        status = {r[0]: (r[1], r[2], r[3]) for r in self.work.con.execute("SELECT event_id, status, transient, error FROM paper_stages WHERE edition = 'iclr-2025'")}
        self.assertEqual(status, {"1": ("ok", 0, None), "2": ("transient", 1, "HTTP 429"), "3": ("failed", 0, "no-html")})
        self.assertIn("retry 1 transient", update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2025"]["need"][0])
        self.assertEqual(out["pending_retries"], 1)
        # a daily timer retries the leftover work after a day, not after the weekly period
        self.assertFalse(update.due(self.work, 7, retry_days=1))
        self.work.set("last_success", (__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
                                       - __import__("datetime").timedelta(days=1.1)).isoformat())
        self.assertTrue(update.due(self.work, 7, retry_days=1))
        self.scenario["network_down"] = False                                 # the network is back: the transient one is fetched again
        out = update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        f = out["editions"]["iclr-2025"]
        self.assertEqual((f["parsed_ok"], f["parsed_failed"], f["transient_now"]), (2, 1, 0))
        self.assertEqual(self.work.con.execute("SELECT attempts FROM paper_stages WHERE event_id = '3'").fetchone()[0], 2)   # recorded, not refetched
        self.assertEqual(update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2025"]["need"], [])
        self.assertEqual(out["pending_retries"], 0)
        self.assertFalse(update.due(self.work, 7, retry_days=1))

    def test_changed_code_decides_the_rerun_scope(self):
        update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.pipe.calls.clear()
        self.bump("bench_mentions.py")                                        # the rules changed: mentions over every parsed paper
        plan = update.status(self.work, self.EDS, self.pipe)["editions"]
        self.assertEqual(plan["iclr-2025"]["need"], ["mentions (rules changed)"])
        update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual([c for c in self.pipe.calls if c[0] in ("parse", "reparse", "mentions", "match")],
                         [("mentions", "iclr-2025"), ("mentions", "iclr-2026")])
        self.pipe.calls.clear()
        self.bump("html_extract.py")                                          # the parser changed: re-parse the archive, then everything after
        self.assertEqual(update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2026"]["need"], ["reparse (parser changed)", "mentions"])
        update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual([c for c in self.pipe.calls if c[1:] == ("iclr-2026",)], [("reparse", "iclr-2026"), ("mentions", "iclr-2026")])
        self.pipe.calls.clear()
        self.scenario["papers"]["iclr-2026"] = [4, 5, 6]                      # a new paper in a feed: normalize changed the file
        self.pipe.normalize(update.edition_paths("ICLR", 2026, self.root))
        self.pipe.calls.clear()
        self.assertEqual(update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2026"]["need"], ["match"])
        update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual([c for c in self.pipe.calls if c[1:] == ("iclr-2026",)],
                         [("match", "iclr-2026"), ("parse", "iclr-2026"), ("mentions", "iclr-2026")])

    def test_changed_evidence_queues_the_reviewed_claim(self):
        update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.scenario["claims"]["2501.00001#toy"]["sha"] = "sha-a2"         # the sentence the reviewer read is gone
        self.bump("bench_mentions.py")
        out = update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual({k: out["claims"][k] for k in ("total", "queued_new", "queued_changed")}, {"total": 2, "queued_new": 0, "queued_changed": 1})
        self.assertEqual({r[0]: r[1] for r in self.work.con.execute("SELECT claim_id, reason FROM claim_queue")},
                         {"2504.00001#fresh": "new", "2501.00001#toy": "changed"})

    def test_adopting_existing_outputs_marks_them_done_without_running_anything(self):
        for disp, year in self.EDS:                                           # outputs exist on disk from an earlier pipeline
            paths = update.edition_paths(disp, year, self.root)
            self.pipe.normalize(paths); self.pipe.match(paths); self.pipe.parse(paths, reparse=False); self.pipe.mentions(paths)
        self.pipe.calls.clear()
        with unittest.mock.patch.object(update, "index_state", return_value=self.pipe.index_state()):
            adopted = update.adopt(self.work, self.EDS)
        self.assertEqual(adopted["iclr-2025"], ["normalized", "matched", "parsed", "mentions"])
        self.assertEqual(update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2025"]["need"], [])
        self.assertEqual(self.work.con.execute("SELECT COUNT(*) FROM paper_stages WHERE status = 'ok'").fetchone()[0], 5)
        self.assertIsNone(self.work.last_success())                           # adopting is not a successful update
        out = update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual([c for c in self.pipe.calls if c[0] not in ("build", "claims")], [])   # nothing redone
        self.assertEqual(out["claims"]["total"], 2)                           # claims compared on the first real run

    def test_discovery_refreshes_sources_and_a_moved_record_is_rechecked_in_a_temporary_path(self):
        self.scenario["dates"] = {"2501.00001": "2025-01-01", "2502.00001": "2025-01-01", "2503.00001": "2025-01-01",
                                  "2504.00001": "2025-04-01", "2505.00001": "2025-04-01"}
        self.work.set("harvest_until", "2026-10-01")
        with unittest.mock.patch.object(update, "open_editions", return_value={"iclr-2026"}):
            out = update.run(self.work, self.pipe, editions=self.EDS)
        harvest = next(c for c in self.pipe.calls if c[0] == "harvest")
        self.assertEqual(harvest[1], "2026-09-30")                           # a day of overlap before the last harvest
        self.assertEqual(out["discovery"]["recollected"], ["iclr-2026"])      # only the open edition's list is re-fetched
        known = self.work.versions_of("iclr-2025")["2501.00001"]
        self.assertEqual((known["source_version"], known["content_sha256"] is not None), ("2025-01-01", True))   # fetched now: confirmed
        # the arXiv record of one paper moved: metadata changed, text the same -> confirmed, nothing rewritten
        self.scenario["dates"]["2501.00001"] = "2026-10-05"
        self.pipe.calls.clear()
        st = update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2025"]
        self.assertEqual(st["recheck"], 1)
        self.assertTrue(any("re-check 1 moved records" in n for n in st["need"]), st["need"])   # (a moved record also changes the index: re-match too)
        ft = update.edition_paths("ICLR", 2025, self.root)["fulltext"]
        before = ft.read_text()
        with unittest.mock.patch.object(update, "open_editions", return_value=set()):
            out = update.run(self.work, self.pipe, editions=self.EDS)
        # the moved record changed the index (re-match) and was re-checked; nothing was parsed anew
        self.assertEqual([c[:2] for c in self.pipe.calls if c[1:2] == ("iclr-2025",)], [("match", "iclr-2025"), ("recheck", "iclr-2025")])   # same text: no mentions
        self.assertEqual(out["editions"]["iclr-2025"]["recheck"], {"same": 1, "changed": 0, "failed": 0, "unavailable": 0, "unknown": 0})
        self.assertEqual(ft.read_text(), before)
        self.assertEqual(self.work.versions_of("iclr-2025")["2501.00001"]["source_version"], "2026-10-05")
        self.assertEqual(update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2025"]["need"], [])
        # the record moves again and this time the text changed: replaced, archived under both hashes, mentions re-run
        self.scenario["dates"]["2501.00001"] = "2026-10-09"
        self.scenario["text_version"] = {"2501.00001": "v2"}
        self.pipe.calls.clear()
        with unittest.mock.patch.object(update, "open_editions", return_value=set()):
            out = update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual(out["editions"]["iclr-2025"]["recheck"]["changed"], 1)
        self.assertIn(("mentions", "iclr-2025"), self.pipe.calls)
        text = next(json.loads(l) for l in ft.read_text().splitlines() if json.loads(l)["arxiv_base"] == "2501.00001")
        self.assertIn("v2", text["sections"][0]["text"])
        archive = update.edition_paths("ICLR", 2025, self.root)["archive"]
        self.assertEqual(len(list(archive.glob("2501.00001.*.html.gz"))), 2)   # both versions kept
        self.assertEqual(self.work.versions_of("iclr-2025")["2501.00001"]["content_sha256"], text["html_sha256"])
        # the record moves once more but the fetch fails: nothing replaced, the failure is recorded and retried
        self.scenario["dates"]["2501.00001"] = "2026-10-12"
        self.scenario["recheck_fails"] = {"2501.00001": True}
        before = ft.read_text()
        with unittest.mock.patch.object(update, "open_editions", return_value=set()):
            out = update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual(out["editions"]["iclr-2025"]["recheck"]["failed"], 1)
        self.assertEqual(ft.read_text(), before)
        v = self.work.versions_of("iclr-2025")["2501.00001"]
        self.assertEqual((v["source_version"], v["recheck_datestamp"], v["recheck_status"]), ("2026-10-09", "2026-10-12", "failed"))
        self.assertEqual(out["pending_retries"], 1)
        self.assertTrue(any("re-check 1 moved records" in n for n in update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2025"]["need"]))
        self.scenario["recheck_fails"] = {}
        with unittest.mock.patch.object(update, "open_editions", return_value=set()):
            out = update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual(out["editions"]["iclr-2025"]["recheck"]["same"], 1)
        self.assertEqual(self.work.versions_of("iclr-2025")["2501.00001"]["recheck_status"], None)

    def test_adopted_parses_have_no_confirmed_version_and_a_new_index_rematches(self):
        for disp, year in self.EDS:
            paths = update.edition_paths(disp, year, self.root)
            self.pipe.normalize(paths); self.pipe.match(paths); self.pipe.parse(paths, reparse=False); self.pipe.mentions(paths)
        self.scenario["dates"] = {"2501.00001": "2025-01-01"}
        with unittest.mock.patch.object(update, "index_state", return_value=self.pipe.index_state()):
            update.adopt(self.work, self.EDS)
        v = self.work.versions_of("iclr-2025")["2501.00001"]
        self.assertEqual((v["source_version"], v["content_sha256"], v["baseline_datestamp"]), (None, None, "2025-01-01"))
        self.pipe.calls.clear()
        with unittest.mock.patch.object(update, "open_editions", return_value=set()):
            update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual(self.work.versions_of("iclr-2025")["2501.00001"]["source_version"], None)   # a build confirms nothing
        self.assertEqual([c for c in self.pipe.calls if c[0] in ("match", "parse", "recheck")], [])
        # new records reach the index: every edition is matched again (unmatched, ambiguous, collided and fuzzy papers meet them)
        self.scenario["dates"]["2599.00001"] = "2026-10-10"
        self.pipe.calls.clear()
        self.assertEqual(update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2026"]["need"], ["match (index changed)"])
        with unittest.mock.patch.object(update, "open_editions", return_value=set()):
            update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual([c for c in self.pipe.calls if c[0] == "match"], [("match", "iclr-2025"), ("match", "iclr-2026")])
        # and the adopted paper's record moving is a re-check against its archived text, not a guessed version
        self.scenario["dates"]["2501.00001"] = "2026-10-11"
        with unittest.mock.patch.object(update, "open_editions", return_value=set()):
            out = update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual(out["editions"]["iclr-2025"]["recheck"], {"same": 1, "changed": 0, "failed": 0, "unavailable": 0, "unknown": 0})
        self.assertEqual(self.work.versions_of("iclr-2025")["2501.00001"]["source_version"], "2026-10-11")

    def test_a_review_change_alone_regenerates_the_catalogue_and_clears_the_queue(self):
        update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual([r[0] for r in self.work.con.execute("SELECT claim_id FROM claim_queue")], ["2504.00001#fresh"])
        self.pipe.calls.clear()
        # the reviewer decides the queued claim; nothing else changed
        self.scenario["reviewed"]["2504.00001#fresh"] = {"label": "E", "status": "confirmed", "evidence_sha": "sha-b"}
        (self.root / "config/introduced_review.json").write_text(json.dumps(self.scenario["reviewed"]))
        out = update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertIn(("claims",), self.pipe.calls)                           # the catalogue is regenerated
        self.assertEqual(out["claims"]["dequeued_reviewed"], 1)
        self.assertEqual(self.work.con.execute("SELECT COUNT(*) FROM claim_queue").fetchone()[0], 0)
        # the sentence changes again after the review: back to the queue as "changed"
        self.scenario["claims"]["2504.00001#fresh"]["sha"] = "sha-b2"
        self.bump("bench_mentions.py")
        out = update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual([tuple(r) for r in self.work.con.execute("SELECT claim_id, reason FROM claim_queue")], [("2504.00001#fresh", "changed")])
        # a confirmed new entity changes the vocabulary: mentions are re-extracted in the same run
        self.pipe.calls.clear()
        original_claims = self.pipe.claims

        def claims_adding_entity():
            (self.root / "config/benchmarks_introduced.json").write_text(json.dumps({"benchmarks": [{"id": "fresh", "name": "Fresh"}, {"id": "newbie", "name": "Newbie"}]}))
            return original_claims()
        self.pipe.claims = claims_adding_entity
        (self.root / "config/introduced_review.json").write_text(json.dumps({**self.scenario["reviewed"], "x": {}}))
        out = update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual(out["claims"].get("mentions_rerun"), "vocabulary changed")
        self.assertEqual([c for c in self.pipe.calls if c[0] == "mentions"], [("mentions", "iclr-2025"), ("mentions", "iclr-2026")])
        self.assertEqual(update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2025"]["need"], [])

    def test_a_latest_text_that_is_unavailable_keeps_the_served_parse_and_is_rejudged_on_schedule(self):
        self.scenario["dates"] = {"2501.00001": "2025-01-01", "2502.00001": "2025-01-01", "2503.00001": "2025-01-01",
                                  "2504.00001": "2025-04-01", "2505.00001": "2025-04-01"}
        with unittest.mock.patch.object(update, "open_editions", return_value=set()):
            update.run(self.work, self.pipe, editions=self.EDS)
        ft = update.edition_paths("ICLR", 2025, self.root)["fulltext"]
        before = ft.read_text()
        v0 = self.work.versions_of("iclr-2025")["2501.00001"]
        # the record moved, but the latest page has no HTML now: the served parse, its hash and version stay
        self.scenario["dates"]["2501.00001"] = "2026-10-05"
        self.scenario["fail"] = {"2501.00001": "permanent"}
        original = self.pipe.recheck

        def recheck_no_html(paths, bases):
            self.pipe.calls.append(("recheck", paths["key"], tuple(bases)))
            return [{"arxiv_base": b, "ok": False, "error": "no-html"} for b in bases], []
        self.pipe.recheck = recheck_no_html
        with unittest.mock.patch.object(update, "open_editions", return_value=set()):
            out = update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual(out["editions"]["iclr-2025"]["recheck"], {"same": 0, "changed": 0, "failed": 0, "unavailable": 1, "unknown": 0})
        self.assertEqual(ft.read_text(), before)                                  # nothing overwritten
        v = self.work.versions_of("iclr-2025")["2501.00001"]
        self.assertEqual((v["source_version"], v["content_sha256"]), (v0["source_version"], v0["content_sha256"]))
        self.assertEqual((v["recheck_status"], v["recheck_datestamp"]), ("unavailable", "2026-10-05"))
        self.assertEqual(self.work.con.execute("SELECT recheck_error FROM paper_stages WHERE arxiv_base = '2501.00001'").fetchone()[0], "no-html")
        # not a retry for the daily schedule, and not due again until the record moves, the parser changes or 30 days pass
        self.assertEqual(out["pending_retries"], 0)
        self.assertEqual(update.status(self.work, self.EDS, self.pipe)["recheck_unavailable"], 1)
        self.assertEqual(update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2025"]["need"], [])
        self.pipe.calls.clear()
        self.scenario["dates"]["2501.00001"] = "2026-10-09"                   # the record moved again: tried again
        self.assertEqual(update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2025"]["recheck"], 1)
        self.pipe.recheck = original
        self.scenario["fail"] = {}
        with unittest.mock.patch.object(update, "open_editions", return_value=set()):
            out = update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual(out["editions"]["iclr-2025"]["recheck"]["same"], 1)
        self.assertIsNone(self.work.versions_of("iclr-2025")["2501.00001"]["recheck_status"])
        # the schedule alone brings an unavailable one back: an attempt older than recheck_days
        self.pipe.recheck = recheck_no_html
        self.scenario["dates"]["2501.00001"] = "2026-10-12"
        with unittest.mock.patch.object(update, "open_editions", return_value=set()):
            update.run(self.work, self.pipe, editions=self.EDS)
        # an unrelated run records the row again (updated_at moves) without trying it: not due by that
        self.work.con.execute("UPDATE paper_stages SET updated_at = '2026-01-01T00:00:00+00:00' WHERE arxiv_base = '2501.00001'")
        self.work.con.commit()
        self.assertEqual(update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2025"]["recheck"], 0)
        # the ATTEMPT older than recheck_days is what brings it back, however recent the row
        self.work.con.execute("UPDATE paper_stages SET last_recheck_at = '2026-01-01T00:00:00+00:00', updated_at = ? WHERE arxiv_base = '2501.00001'", (update.now(),))
        self.work.con.commit()
        self.assertEqual(update.status(self.work, self.EDS, self.pipe)["editions"]["iclr-2025"]["recheck"], 1)

    def test_a_vanished_reviewed_claim_is_queued_with_its_last_sentence_and_its_benchmark_is_kept_for_a_decision(self):
        update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual(self.work.claim_record("2501.00001#toy")["evidence"], "We introduce Toy.")
        # the paper's new text no longer says it: the claim is gone from the extraction
        del self.scenario["claims"]["2501.00001#toy"]
        self.bump("bench_mentions.py")
        out = update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual((out["claims"]["queued_vanished"], out["claims"]["queued_new"]), (1, 0))
        queue = {r[0]: r[1] for r in self.work.con.execute("SELECT claim_id, reason FROM claim_queue")}
        self.assertEqual(queue["2501.00001#toy"], "vanished")
        record = self.work.claim_record("2501.00001#toy")
        self.assertEqual((record["evidence"], record["vanished_run"] is not None), ("We introduce Toy.", True))   # history kept
        self.assertIn(("retain", "2501.00001#toy"), self.pipe.calls)              # the reviewed claim is retained for a decision
        self.assertEqual(self.scenario["reviewed"]["2501.00001#toy"]["retained_evidence"]["evidence"], "We introduce Toy.")
        self.assertEqual(self.scenario["reviewed"]["2501.00001#toy"]["retained_evidence"]["reason"], "sentence_not_extracted")   # the paper is still matched
        # a decision on the vanished claim takes it out of the queue; the history stays
        self.scenario["reviewed"]["2501.00001#toy"]["vanished_decision"] = {"decision": "retain", "reason": "introduced in v1", "reviewed_by": "test"}
        self.bump("bench_mentions.py")
        out = update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertNotIn("2501.00001#toy", {r[0] for r in self.work.con.execute("SELECT claim_id FROM claim_queue")})
        self.assertEqual(self.work.claim_record("2501.00001#toy")["vanished_run"] is not None, True)
        # the same for retire, on a reviewed claim that vanishes later
        self.scenario["reviewed"]["2504.00001#fresh"] = {"status": "confirmed", "label": "E", "evidence_sha": "sha-b"}
        self.pipe.calls.clear()
        del self.scenario["claims"]["2504.00001#fresh"]
        self.bump("bench_mentions.py")
        out = update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual(out["claims"]["queued_vanished"], 1)
        self.assertIn(("retain", "2504.00001#fresh"), self.pipe.calls)
        self.scenario["reviewed"]["2504.00001#fresh"]["vanished_decision"] = {"decision": "retire", "reason": "no longer claimed", "reviewed_by": "test"}
        self.bump("bench_mentions.py")
        update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual({r[0] for r in self.work.con.execute("SELECT claim_id FROM claim_queue WHERE reason = 'vanished'")}, set())
        self.assertEqual(self.work.claim_record("2504.00001#fresh")["evidence"], "We introduce Fresh.")
        # an unreviewed vanished claim is queued too, with no retention
        self.scenario["claims"]["2505.00001#late"] = {"edition": "iclr-2026", "sha": "sha-c", "evidence": "We introduce Late.", "name": "Late", "noun": "benchmark", "arxiv_base": "2505.00001", "title": "Paper 5"}
        self.bump("bench_mentions.py")
        update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.pipe.calls.clear()
        del self.scenario["claims"]["2505.00001#late"]
        self.bump("bench_mentions.py")
        out = update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual(out["claims"]["queued_vanished"], 1)
        self.assertNotIn(("retain", "2505.00001#late"), self.pipe.calls)

    def test_a_stale_review_is_not_reused_and_a_vanished_claim_waits_for_a_decision(self):
        from icml.bench_mentions import review_for
        claim = {"claim_id": "2501.00001#toy", "evidence": "We introduce Toy, a benchmark.", "name": "Toy"}
        import hashlib
        sha = hashlib.sha256(claim["evidence"].encode()).hexdigest()[:16]
        entry = {"label": "X", "status": "excluded", "note": "real name RealToy; E", "evidence_sha": sha,
                 "target": {"benchmark_id": "realtoy", "decision": "same", "evidence": "..."}}
        self.assertEqual(review_for(claim, entry), entry)                          # same sentence: the review applies, correction included
        changed = {**claim, "evidence": "We use RealToy for evaluation."}
        self.assertEqual(review_for(changed, entry), {"status": "stale", "label": "X", "stale": "evidence changed since review"})   # nothing reused
        vanished = {**claim, "vanished": True}
        confirmed = {"label": "E", "status": "confirmed", "evidence_sha": sha, "retained_evidence": {"evidence": claim["evidence"]}}
        self.assertTrue(review_for(vanished, confirmed)["vanished_in_latest_version"])
        self.assertEqual(review_for(vanished, {**confirmed, "vanished_decision": {"decision": "retire", "reason": "no longer claimed"}})["status"], "excluded")
        self.assertEqual(review_for(vanished, {**confirmed, "vanished_decision": {"decision": "retain", "reason": "introduced in v1"}})["status"], "confirmed")
        # a retained claim keeps the title and authors recorded with it when the paper is no longer in the index
        from icml.bench_mentions import retained_claim
        kept = {"name": "Toy", "noun": "benchmark", "edition": "iclr-2025", "arxiv_base": "2501.00001", "title": "Paper 1",
                "authors": ["A. Author"], "evidence": "We introduce Toy, a benchmark."}
        c = retained_claim("2501.00001#toy", kept, {})
        self.assertEqual((c["title"], c["authors"], c["vanished"]), ("Paper 1", ["A. Author"], True))

    def test_a_failed_run_is_recorded_and_the_next_one_proceeds(self):
        def broken(paths, **kw):
            raise RuntimeError("arXiv unreachable")
        self.pipe.parse = broken
        with self.assertRaisesRegex(RuntimeError, "unreachable"):
            update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        run = self.work.con.execute("SELECT outcome, error FROM runs").fetchone()
        self.assertEqual((run[0], "unreachable" in run[1]), ("failed", True))
        self.assertIsNone(self.work.last_success())
        self.pipe.parse = FakePipeline.parse.__get__(self.pipe)
        update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertEqual([r[0] for r in self.work.con.execute("SELECT outcome FROM runs ORDER BY run_id")], ["failed", "ok"])

    def test_one_run_at_a_time_and_due_check(self):
        with update.Locked(self.root):
            with self.assertRaisesRegex(RuntimeError, "another update is running"):
                with update.Locked(self.root):
                    pass
            # another process is refused too
            code = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(Path(update.__file__).parents[1])!r}); "
                                   f"from icml import update; from pathlib import Path\n"
                                   f"try:\n    update.Locked(Path({str(self.root)!r})).__enter__(); print('acquired')\n"
                                   f"except RuntimeError as e:\n    print('refused')"], capture_output=True, text=True)
            self.assertIn("refused", code.stdout)
        with update.Locked(self.root):                                      # released: the lock can be taken again
            pass
        self.assertTrue(update.due(self.work, 7))                             # never ran: due
        update.run(self.work, self.pipe, editions=self.EDS, discover=False)
        self.assertFalse(update.due(self.work, 7))
        self.work.set("last_success", "2026-01-01T00:00:00+00:00")           # a missed week: due once, at the next start
        self.assertTrue(update.due(self.work, 7))


if __name__ == "__main__":
    unittest.main()
