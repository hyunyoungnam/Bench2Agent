"""The arXiv index: the OAI header datestamp is the record's stamp, a record
whose stamp moved is appended, the same window harvested twice adds nothing,
and the matcher reads the newest row per paper."""
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from icml import arxiv_harvest as h

OAI = "http://www.openarchives.org/OAI/2.0/"
DC = "http://purl.org/dc/elements/1.1/"


def record(aid, title, datestamp, date="2025-01-01"):
    return f"""<record xmlns="{OAI}"><header><identifier>oai:arXiv.org:{aid}</identifier><datestamp>{datestamp}</datestamp></header>
    <metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="{DC}">
    <dc:title>{title}</dc:title><dc:date>{date}</dc:date></oai_dc:dc></metadata></record>"""


def listing(*records, token=""):
    return ET.fromstring(f"""<OAI-PMH xmlns="{OAI}"><ListRecords>{''.join(records)}<resumptionToken>{token}</resumptionToken></ListRecords></OAI-PMH>""")


class HarvestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.out, self.state = root / "oai_index.jsonl", root / "oai_state.json"
        for p in (patch.object(h, "OUT", self.out), patch.object(h, "STATE", self.state), patch.object(h.time, "sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)

    def rows(self):
        return [json.loads(l) for l in self.out.read_text().splitlines()] if self.out.exists() else []

    def test_header_datestamp_is_the_stamp_and_a_moved_record_is_appended_once(self):
        first = listing(record("2501.00001", "A paper", "2026-09-01"))
        with patch.object(h, "request", return_value=first):
            h.harvest_set("cs", "2026-08-01", {}, 0, "2026-09-02")
        self.assertEqual([(r["arxiv_base"], r["datestamp"], r["date"]) for r in self.rows()], [("2501.00001", "2026-09-01", "2025-01-01")])
        # same dc:date, only the header datestamp moved: a changed record, appended
        moved = listing(record("2501.00001", "A paper", "2026-10-03"))
        with patch.object(h, "request", return_value=moved):
            h.harvest_set("cs", "2026-10-01", {}, 0, "2026-10-04")
        self.assertEqual([r["datestamp"] for r in self.rows()], ["2026-09-01", "2026-10-03"])
        # the same window again: recorded complete, skipped; with --redo it runs and adds nothing
        state = {"cs@2026-10-01..2026-10-04": {"token": None, "complete": True}}
        with patch.object(h, "request", return_value=moved) as req:
            self.assertEqual(h.harvest_set("cs", "2026-10-01", state, 0, "2026-10-04"), 0)
            req.assert_not_called()
            self.assertEqual(h.harvest_set("cs", "2026-10-01", state, 0, "2026-10-04", redo=True), 0)
            req.assert_called_once()
        self.assertEqual(len(self.rows()), 2)
        # an older stamp seen later (another set listing the record) is not appended
        older = listing(record("2501.00001", "A paper", "2026-09-15"))
        with patch.object(h, "request", return_value=older):
            h.harvest_set("stat", "2026-09-10", {}, 0, "2026-09-16")
        self.assertEqual(len(self.rows()), 2)

    def test_matcher_and_updater_read_the_newest_row_per_paper(self):
        from icml import update
        root = Path(self.tmp.name)
        (root / "data/raw/arxiv").mkdir(parents=True)
        idx = root / "data/raw/arxiv/oai_index.jsonl"
        idx.write_text("\n".join(json.dumps(r) for r in [
            {"arxiv_base": "2501.00001", "title": "Old title", "date": "2025-01-01", "datestamp": "2026-09-01"},
            {"arxiv_base": "2501.00001", "title": "New title", "date": "2025-01-01", "datestamp": "2026-10-03"},
            {"arxiv_base": "2502.00002", "title": "Other", "date": "2025-02-01"}]) + "\n")
        state = update.index_state(root)
        self.assertEqual(state["stamps"], {"2501.00001": "2026-10-03", "2502.00002": "2025-02-01"})
        self.assertEqual(len(state["hash"]), 16)
        idx.write_text(idx.read_text() + json.dumps({"arxiv_base": "2503.00003", "title": "Third", "date": "2025-03-01", "datestamp": "2026-10-05"}) + "\n")
        self.assertNotEqual(update.index_state(root)["hash"], state["hash"])     # any new record changes the index identity


if __name__ == "__main__":
    unittest.main()
