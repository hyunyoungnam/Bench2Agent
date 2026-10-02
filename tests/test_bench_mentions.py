"""Boundary cases for the rule that turns a text n-gram into a benchmark mention."""
import unittest

from icml.bench_mentions import Finder, caption_role, role_at, stated_roles
from icml.taxonomy import dataset_key


def finder(registered: dict[str, list[str]], provisional: list[str]) -> Finder:
    fold_to, casings = {}, {}
    for key, names in registered.items():
        casings[key] = {names[0]}
        for n in names:
            fold_to[dataset_key(n)] = key
    for n in provisional:
        fold_to.setdefault(dataset_key(n), "k:" + dataset_key(n))
    return Finder(fold_to, casings)


F = finder({"math": ["MATH"], "c4": ["C4"], "cifar-10": ["CIFAR-10"],
            "natural-questions": ["Natural Questions", "nq"]},
           ["nuScenes", "code", "Once"])


def keys(text: str) -> list[str]:
    return [k for k, *_ in F.find(text)]


class MatchRuleTests(unittest.TestCase):
    def test_registered_casing_counts_and_prose_does_not(self):
        self.assertEqual(keys("We evaluate on MATH and GSM8K."), ["math"])
        self.assertEqual(keys("Strong math reasoning. Math is hard."), [])

    def test_spelling_variants_fold_to_one_benchmark(self):
        self.assertEqual(keys("CIFAR10 and CIFAR-10 (Krizhevsky, 2009)"), ["cifar-10", "cifar-10"])

    def test_multi_word_name_is_one_mention(self):
        self.assertEqual(keys("on Natural Questions, we"), ["natural-questions"])

    def test_name_shape_admits_unregistered_names_only(self):
        self.assertEqual(keys("results on nuScenes."), ["k:nuscenes"])
        self.assertEqual(keys("Our code is available. Once trained,"), [])

    def test_section_numbers_and_notation_are_not_names(self):
        self.assertEqual(keys("see Appendix C.4 where c=4"), [])
        self.assertEqual(keys("trained on C4 for one epoch"), ["c4"])
        self.assertEqual(keys("with c4 = 0.1"), [])

    def test_digit_must_touch_a_letter(self):
        self.assertEqual(keys("as explained in the text 8 times"), [])

    def test_prose_words_after_a_name_are_not_part_of_it(self):
        self.assertEqual(keys("on CIFAR-10 dataset splits"), ["cifar-10"])


G = finder({"cifar-10": ["CIFAR-10"], "c4": ["C4"], "gsm8k": ["GSM8K"], "imagenet-1k": ["ImageNet-1k"]},
           ["MS-MARCO", "CIFAR-10-C"])


def roles(sentence: str) -> dict[str, str | None]:
    cues = stated_roles(sentence)
    return {k: role_at(cues, t) for k, _, t in G.find(sentence)}


class StatedRoleTests(unittest.TestCase):
    def test_first_person_training_is_trains_on(self):
        self.assertEqual(roles("We fine-tune Llama on C4 for one epoch."), {"c4": "trains_on"})
        self.assertEqual(roles("Our training data consists of C4."), {"c4": "trains_on"})

    def test_training_then_evaluation_in_one_sentence(self):
        self.assertEqual(roles("We train on C4 and evaluate on GSM8K."),
                         {"c4": "trains_on", "gsm8k": "evaluates_on"})

    def test_someone_elses_training_assigns_nothing(self):
        self.assertEqual(roles("They were trained on MS-MARCO."), {"k:msmarco": None})
        # conservative by design: the passive cue blocks CIFAR-10-C too
        self.assertIsNone(roles("We evaluate models trained on ImageNet-1k on CIFAR-10-C.")["imagenet-1k"])

    def test_a_name_with_no_cue_has_no_role(self):
        self.assertEqual(roles("CIFAR-10 contains 60,000 images."), {"cifar-10": None})

    def test_captions(self):
        self.assertEqual(caption_role("Table 12: Training dataset of each stage."), "trains_on")
        self.assertEqual(caption_role("Table 2: Results on GSM8K and MATH."), "evaluates_on")
        self.assertIsNone(caption_role("Table 1: Statistics of the datasets."))
        self.assertIsNone(caption_role("Table 4: Comparison between our data and existing "
                                       "instruction tuning datasets."))
        self.assertEqual(caption_role("Table 3: Performance comparison on GSM8K."), "evaluates_on")


if __name__ == "__main__":
    unittest.main()
