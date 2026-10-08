"""Migration preserves research identity, saved answers and existing target files."""
import json
import tempfile
import unittest
from pathlib import Path

from bench2agent.data import MEMBER
from bench2agent.migration import migrate
from bench2agent.session import Session, conversations


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = Path(self.tmp.name) / "old"
        self.new = Path(self.tmp.name) / "new"
        snapshot = self.old / MEMBER
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(json.dumps({"schema_version": 1, "source": "arxiv_html_stated_roles",
            "snapshot_id": "original-revision", "editions": {}, "benchmarks": [], "papers": []}))
        previous = self.old / "data/benchtrend"
        (previous / "chats").mkdir(parents=True)
        (previous / "settings.json").write_text(json.dumps({"provider": "openai", "model": "saved-model",
                                                          "language": "en", "unrelated": "omit"}))
        self.cid = "012345abcdef"
        (previous / "chats" / (self.cid + ".json")).write_text(json.dumps({
            "id": self.cid, "provider": "openai", "model": "saved-model", "title": "Existing answer",
            "snapshot_id": "original-revision", "messages": [], "turns": [], "ts": 1}))

    def test_migration_keeps_snapshot_and_resumable_conversation(self):
        result = migrate(self.new, self.old)
        self.assertTrue(result["snapshot_copied"])
        self.assertTrue(result["settings_copied"])
        self.assertEqual(result["chats_copied"], 1)
        self.assertEqual((self.old / MEMBER).read_bytes(), (self.new / MEMBER).read_bytes())
        self.assertEqual(Session(self.new, "openai", "other-model", cid=self.cid).doc["snapshot_id"], "original-revision")
        self.assertEqual(conversations(self.new)[0]["title"], "Existing answer")
        self.assertNotIn("unrelated", json.loads((self.new / "data/bench2agent/settings.json").read_text()))
        self.assertTrue((self.old / "data/benchtrend/chats" / (self.cid + ".json")).exists())

    def test_repeat_migration_never_overwrites_target_files(self):
        migrate(self.new, self.old)
        target = self.new / "data/bench2agent/settings.json"
        target.write_text('{"model": "new-choice"}')
        result = migrate(self.new, self.old)
        self.assertFalse(result["snapshot_copied"])
        self.assertFalse(result["settings_copied"])
        self.assertEqual(result["chats_copied"], 0)
        self.assertEqual(json.loads(target.read_text())["model"], "new-choice")

    def test_invalid_snapshot_is_not_installed(self):
        (self.old / MEMBER).write_text('{}')
        with self.assertRaises(ValueError):
            migrate(self.new, self.old)
        self.assertFalse((self.new / MEMBER).exists())
