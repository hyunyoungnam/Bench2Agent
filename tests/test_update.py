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
from pathlib import Path

from icml import update


class FakePipeline(update.Pipeline):
    """Stages that write the files the real ones would, without the network."""

    def __init__(self, work, scenario):
        super().__init__(work, parser_python=sys.executable)
        self.s = scenario
        self.calls = []

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
        self._parse(paths, reparse=reparse)
        return "done — fake"

    def _parse(self, paths, *, reparse):
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
        for line in paths["resolved"].read_text().splitlines():
            base = json.loads(line)["arxiv_base"]
            if base in done:
                continue
            fail = self.s.get("fail", {}).get(base)
            if fail == "transient" and self.s.get("network_down"):
                rows.append({"arxiv_base": base, "ok": False, "error": "URLError", "transient": True})
            elif fail == "permanent":
                rows.append({"arxiv_base": base, "ok": False, "error": "no-html"})
            else:
                rows.append({"arxiv_base": base, "ok": True, "sections": [{"bucket": "experiments", "text": f"We evaluate on Toy ({base})."}]})
        ft.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    def mentions(self, paths):
        self.calls.append(("mentions", paths["key"]))
        paths["mentions"].parent.mkdir(parents=True, exist_ok=True)
        rows = [json.loads(l) for l in paths["fulltext"].read_text().splitlines()]
        paths["mentions"].write_text("\n".join(json.dumps({"arxiv_base": r["arxiv_base"], "mentions": {}}) for r in rows if r.get("ok")) + "\n")

    def claims(self):
        self.calls.append(("claims",))
        return dict(self.s["claims"])

    def reviewed(self):
        return dict(self.s.get("reviewed", {}))

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
        self.scenario = {"papers": {"iclr-2025": [1, 2, 3], "iclr-2026": [4, 5]},
                         "claims": {"2501.00001#toy": ("iclr-2025", "sha-a"), "2504.00001#fresh": ("iclr-2026", "sha-b")},
                         "reviewed": {"2501.00001#toy": {"label": "E", "status": "confirmed", "evidence_sha": "sha-a"}}}
        self.pipe = FakePipeline(self.work, self.scenario)

    def bump(self, name):
        p = self.root / "src/icml" / name
        p.write_text(p.read_text() + "# changed\n")

    def test_first_run_takes_every_edition_through_every_stage_and_queues_new_claims(self):
        dry = update.run(self.work, self.pipe, editions=self.EDS, dry_run=True)
        self.assertEqual(dry["editions"]["iclr-2025"]["need"], ["normalize", "match"])
        self.assertEqual(self.pipe.calls, [])                                  # a dry run runs nothing
        out = update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual(out["editions"]["iclr-2025"]["did"], ["normalize", "match", "parse", "mentions"])
        self.assertEqual((out["editions"]["iclr-2025"]["parsed_ok"], out["editions"]["iclr-2025"]["mentions"]), (3, 3))
        self.assertEqual(out["claims"], {"total": 2, "queued_new": 1, "queued_changed": 0})   # the reviewed one is not queued
        self.assertEqual(out["build"]["revision_id"], "rev-" + str(len(self.pipe.calls)))
        rows = self.work.con.execute("SELECT edition, event_id, status FROM paper_stages ORDER BY edition, event_id").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("iclr-2025", "1", "ok"), ("iclr-2025", "2", "ok"), ("iclr-2025", "3", "ok"),
                                                   ("iclr-2026", "4", "ok"), ("iclr-2026", "5", "ok")])
        self.assertEqual([tuple(r) for r in self.work.con.execute("SELECT claim_id, reason FROM claim_queue")], [("2504.00001#fresh", "new")])
        self.assertIsNotNone(self.work.last_success())
        # nothing changed: the next run re-runs no stage, builds again
        before = list(self.pipe.calls)
        again = update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual([c for c in self.pipe.calls[len(before):] if c[0] != "build"], [])
        self.assertEqual(again["claims"], {})                                 # nothing touched: claims not recomputed

    def test_transient_failures_are_retried_and_permanent_ones_are_kept(self):
        self.scenario["fail"] = {"2502.00001": "transient", "2503.00001": "permanent"}
        self.scenario["network_down"] = True
        out = update.run(self.work, self.pipe, editions=self.EDS)
        f = out["editions"]["iclr-2025"]
        self.assertEqual((f["parsed_ok"], f["parsed_failed"], f["parsed_transient"]), (1, 1, 1))
        status = {r[0]: (r[1], r[2]) for r in self.work.con.execute("SELECT event_id, status, transient FROM paper_stages WHERE edition = 'iclr-2025'")}
        self.assertEqual(status, {"1": ("ok", 0), "2": ("failed", 1), "3": ("failed", 0)})
        self.assertIn("retry 1 transient", update.status(self.work, self.EDS)["editions"]["iclr-2025"]["need"][0])
        self.scenario["network_down"] = False                                 # the network is back: the transient one is fetched again
        out = update.run(self.work, self.pipe, editions=self.EDS)
        f = out["editions"]["iclr-2025"]
        self.assertEqual((f["parsed_ok"], f["parsed_failed"], f["parsed_transient"]), (2, 1, 0))
        self.assertEqual(self.work.con.execute("SELECT attempts FROM paper_stages WHERE event_id = '3'").fetchone()[0], 2)   # recorded, not refetched
        self.assertEqual(update.status(self.work, self.EDS)["editions"]["iclr-2025"]["need"], [])

    def test_changed_code_decides_the_rerun_scope(self):
        update.run(self.work, self.pipe, editions=self.EDS)
        self.pipe.calls.clear()
        self.bump("bench_mentions.py")                                        # the rules changed: mentions over every parsed paper
        plan = update.status(self.work, self.EDS)["editions"]
        self.assertEqual(plan["iclr-2025"]["need"], ["mentions (rules changed)"])
        update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual([c for c in self.pipe.calls if c[0] in ("parse", "reparse", "mentions", "match")],
                         [("mentions", "iclr-2025"), ("mentions", "iclr-2026")])
        self.pipe.calls.clear()
        self.bump("html_extract.py")                                          # the parser changed: re-parse the archive, then everything after
        self.assertEqual(update.status(self.work, self.EDS)["editions"]["iclr-2026"]["need"], ["reparse (parser changed)", "mentions"])
        update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual([c for c in self.pipe.calls if c[1:] == ("iclr-2026",)], [("reparse", "iclr-2026"), ("mentions", "iclr-2026")])
        self.pipe.calls.clear()
        self.scenario["papers"]["iclr-2026"] = [4, 5, 6]                      # a new paper in a feed: normalize changed the file
        self.pipe.normalize(update.edition_paths("ICLR", 2026, self.root))
        self.pipe.calls.clear()
        self.assertEqual(update.status(self.work, self.EDS)["editions"]["iclr-2026"]["need"], ["match"])
        update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual([c for c in self.pipe.calls if c[1:] == ("iclr-2026",)],
                         [("match", "iclr-2026"), ("parse", "iclr-2026"), ("mentions", "iclr-2026")])

    def test_changed_evidence_queues_the_reviewed_claim(self):
        update.run(self.work, self.pipe, editions=self.EDS)
        self.scenario["claims"]["2501.00001#toy"] = ("iclr-2025", "sha-a2")   # the sentence the reviewer read is gone
        self.bump("bench_mentions.py")
        out = update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual(out["claims"], {"total": 2, "queued_new": 0, "queued_changed": 1})
        self.assertEqual({r[0]: r[1] for r in self.work.con.execute("SELECT claim_id, reason FROM claim_queue")},
                         {"2504.00001#fresh": "new", "2501.00001#toy": "changed"})

    def test_adopting_existing_outputs_marks_them_done_without_running_anything(self):
        for disp, year in self.EDS:                                           # outputs exist on disk from an earlier pipeline
            paths = update.edition_paths(disp, year, self.root)
            self.pipe.normalize(paths); self.pipe.match(paths); self.pipe.parse(paths, reparse=False); self.pipe.mentions(paths)
        self.pipe.calls.clear()
        adopted = update.adopt(self.work, self.EDS)
        self.assertEqual(adopted["iclr-2025"], ["normalized", "matched", "parsed", "mentions"])
        self.assertEqual(update.status(self.work, self.EDS)["editions"]["iclr-2025"]["need"], [])
        self.assertEqual(self.work.con.execute("SELECT COUNT(*) FROM paper_stages WHERE status = 'ok'").fetchone()[0], 5)
        self.assertIsNone(self.work.last_success())                           # adopting is not a successful update
        out = update.run(self.work, self.pipe, editions=self.EDS)
        self.assertEqual([c for c in self.pipe.calls if c[0] not in ("build", "claims")], [])   # nothing redone
        self.assertEqual(out["claims"]["total"], 2)                           # claims compared on the first real run

    def test_a_failed_run_is_recorded_and_the_next_one_proceeds(self):
        def broken(paths, **kw):
            raise RuntimeError("arXiv unreachable")
        self.pipe.parse = broken
        with self.assertRaisesRegex(RuntimeError, "unreachable"):
            update.run(self.work, self.pipe, editions=self.EDS)
        run = self.work.con.execute("SELECT outcome, error FROM runs").fetchone()
        self.assertEqual((run[0], "unreachable" in run[1]), ("failed", True))
        self.assertIsNone(self.work.last_success())
        self.pipe.parse = FakePipeline.parse.__get__(self.pipe)
        update.run(self.work, self.pipe, editions=self.EDS)
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
        update.run(self.work, self.pipe, editions=self.EDS)
        self.assertFalse(update.due(self.work, 7))
        self.work.set("last_success", "2026-01-01T00:00:00+00:00")           # a missed week: due once, at the next start
        self.assertTrue(update.due(self.work, 7))


if __name__ == "__main__":
    unittest.main()
