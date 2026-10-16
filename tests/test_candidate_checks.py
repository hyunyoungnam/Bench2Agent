import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from candidate_checks import label_origins_match  # noqa: E402


class LabelOriginRuleTests(unittest.TestCase):
    prev = {"label_origins": {"declared_area": 2724, "declared_subarea": 2724, "derived_topic": 1517}}

    def test_exactly_the_added_papers_origins_pass(self):
        new = {"label_origins": {"declared_area": 2725, "declared_subarea": 2725, "derived_topic": 1517}}
        self.assertTrue(label_origins_match(self.prev, new, [{"declared_area", "declared_subarea"}]))

    def test_a_wrong_increase_fails(self):
        new = {"label_origins": {"declared_area": 2726, "declared_subarea": 2725, "derived_topic": 1517}}
        self.assertFalse(label_origins_match(self.prev, new, [{"declared_area", "declared_subarea"}]))

    def test_an_existing_paper_relabelled_fails(self):
        # no paper added, but one moved from a derived topic to a declared area
        new = {"label_origins": {"declared_area": 2725, "declared_subarea": 2724, "derived_topic": 1516}}
        self.assertFalse(label_origins_match(self.prev, new, []))

    def test_another_extra_field_changing_fails(self):
        self.assertFalse(label_origins_match({**self.prev, "x": 1}, {**self.prev, "x": 2}, []))


if __name__ == "__main__":
    unittest.main()
