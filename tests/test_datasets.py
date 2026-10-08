"""Boundary examples for a paper-claimed introduced benchmark export."""
import unittest

from bench2agent.core.datasets import _intro_claim, to_csv


class IntroducedBenchmarkTests(unittest.TestCase):
    def test_own_benchmark_with_matching_release(self):
        abstract = "We introduce SPORTU, a benchmark for sports reasoning."
        claim = _intro_claim(abstract, "Our data are available online.",
                             "chili-lab/sportu")
        self.assertEqual(claim, ("abstract", abstract))

    def test_prior_benchmark_link_is_not_introduction(self):
        text = "We mainly build on publicly available CLIP Benchmark code."
        self.assertIsNone(_intro_claim("", text, "openai/CLIP"))

    def test_new_dataset_is_not_confused_with_new_benchmark(self):
        text = ("We introduce OmniRewardBench, a benchmark for evaluation, "
                "and construct OmniRewardData, a training dataset.")
        self.assertIsNone(_intro_claim(text, "Our data are public.",
                                        "team/OmniRewardData"))

    def test_model_evaluated_on_prior_benchmark_is_not_introduction(self):
        text = "We propose ModelX and evaluate it on the ImageNet benchmark."
        self.assertIsNone(_intro_claim(text, "Our code is public.", "team/ModelX"))

    def test_url_slug_alone_is_not_evidence(self):
        text = "We introduce a new benchmark and release it."
        self.assertIsNone(_intro_claim(text, "See https://github.com/team/SPORTU",
                                        "team/SPORTU"))

    def test_ownership_without_new_claim_is_not_introduction(self):
        self.assertIsNone(_intro_claim("", "Our benchmark is publicly available.",
                                        "team/sportu"))

    def test_csv_evidence_with_commas_is_quoted(self):
        out = to_csv(["gid", "evidence"], [[156, "We introduce A, a benchmark."]])
        self.assertIn('156,"We introduce A, a benchmark."', out)


if __name__ == "__main__":
    unittest.main()
