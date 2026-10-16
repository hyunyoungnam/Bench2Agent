"""Connected figures: a printed number is verified only against a tool value whose
context (benchmark, edition, role, author group, metric) agrees with the number's
own clause. Regression cases from a real answer (2026-10-16: "63 editions" passed
because 63 was another benchmark's count)."""
import unittest

from bench2agent.core import figures


def log(*entries):
    return figures.TurnLog([{"name": n, "arguments": a, "result": r, "revision": "rev1"} for n, a, r in entries])


SCOPE = ("benchmark_scope", {}, {"generated_at": "2026-10-15T07:32:21+00:00",
                                 "editions": {f"iclr-{y}": {"total_papers": 100 + y % 100, "parsed_papers": 80} for y in range(1965, 2026)}})
NEW = ("new_benchmarks", {}, {"results": [
    {"id": "olympiadbench", "name": "OlympiadBench", "adoption": {"evaluates_on": {"others": 63, "self": 1, "undetermined": 0}}},
    {"id": "toybench", "name": "ToyBench", "adoption": {"evaluates_on": {"others": 5, "self": 0, "undetermined": 0},
                                                        "trains_on": {"others": 7, "self": 0, "undetermined": 0}}},
    {"id": "otherbench", "name": "OtherBench", "adoption": {"evaluates_on": {"others": 3, "self": 2, "undetermined": 0}}}]})
TREND = ("benchmark_trend", {"benchmark": "toybench"}, {"id": "toybench", "name": "ToyBench", "results": [
    {"edition": "iclr-2025", "papers": 10, "parsed_papers": 3059}, {"edition": "iclr-2026", "papers": 12, "parsed_papers": 4252}]})


def verdicts(text, lg):
    return [(raw, v) for _a, _b, raw, v, _w in figures.scan_connected(text, figures.facts(lg), figures.date_strings(lg), set())]


class ConnectedFigureTests(unittest.TestCase):
    def setUp(self):
        self.lg = log(SCOPE, NEW, TREND)

    def test_an_edition_count_is_checked_against_the_edition_count_not_any_63(self):
        self.assertEqual(len(SCOPE[2]["editions"]), 61)
        self.assertEqual(verdicts("- 분석된 에디션: 63개 (ICLR 1965–2025)", self.lg), [("63", "no")])
        self.assertEqual(verdicts("- 분석된 에디션: 61개", self.lg), [("61", "ok")])
        self.assertEqual(verdicts("We analysed 63 editions.", self.lg), [("63", "no")])
        self.assertEqual(verdicts("We analysed 61 editions.", self.lg), [("61", "ok")])

    def test_the_same_number_on_another_benchmark_edition_or_role_does_not_pass(self):
        # benchmark: ToyBench's others are 5; 3 is OtherBench's
        self.assertEqual(verdicts("ToyBench: 타저자 3편", self.lg), [("3", "no")])
        self.assertEqual(verdicts("OtherBench: 타저자 3편", self.lg), [("3", "ok")])
        # edition: 12 papers is ICLR 2026, not 2025
        self.assertEqual(verdicts("ToyBench는 ICLR 2025에서 12편", self.lg), [("12", "no")])
        self.assertEqual(verdicts("ToyBench는 ICLR 2026에서 12편", self.lg), [("12", "ok")])
        # role: ToyBench's 7 others are training use, not evaluation
        self.assertEqual(verdicts("ToyBench 평가 사용: 타저자 7편", self.lg), [("7", "no")])
        self.assertEqual(verdicts("ToyBench 학습 사용: 타저자 7편", self.lg), [("7", "ok")])

    def test_a_number_with_no_connecting_cue_stays_unverified(self):
        self.assertEqual(verdicts("There are 63 of them.", self.lg), [("63", "na")])
        self.assertEqual(verdicts("ToyBench는 5번째로 소개", self.lg), [("5", "na")])     # a name alone says where, not what

    def test_a_section_heading_names_the_benchmark(self):
        text = "### OtherBench\n- 타저자 3편, 자기 2편\n### ToyBench\n- 타저자 3편"
        self.assertEqual(verdicts(text, self.lg), [("3", "ok"), ("2", "ok"), ("3", "no")])

    def test_an_edition_series_reads_count_over_denominator(self):
        text = "### ToyBench\nICLR 추이: iclr-2025 10/3059, iclr-2026 12/4252"
        self.assertEqual(verdicts(text, self.lg), [("10", "ok"), ("3059", "ok"), ("12", "ok"), ("4252", "ok")])
        self.assertEqual(verdicts("### ToyBench\niclr-2025 12/3059", self.lg), [("12", "no"), ("3059", "ok")])

    def test_one_name_spelled_two_ways_is_one_benchmark(self):
        lg = log(("benchmark_trend", {"benchmark": "noisy-mnist"}, {"id": "noisy-mnist", "name": "Noisy-MNIST",
                  "results": [{"edition": "iclr-2025", "papers": 0, "parsed_papers": 3060}]}))
        self.assertEqual(verdicts("## Noisy MNIST\n- iclr-2025 0/3060", lg)[:1], [("0", "ok")])   # one benchmark in the log: no name to violate
        lg2 = log(("benchmark_trend", {"benchmark": "noisy-mnist"}, {"id": "noisy-mnist", "name": "Noisy-MNIST",
                   "results": [{"edition": "iclr-2025", "papers": 0, "parsed_papers": 3060}]}),
                  ("benchmark_adoption", {"benchmark": "noisy-mnist-2"}, {"id": "noisy-mnist-2", "name": "Noisy MNIST", "before_claim": 0}))
        self.assertEqual(verdicts("## Noisy MNIST\n- iclr-2025 0/3060", lg2), [("0", "ok"), ("3060", "ok")])

    def test_dates_are_not_statistics_and_are_never_counted_as_verified_figures(self):
        found = figures.scan_connected("생성 2026-10-15입니다. 확인 2026-10-17입니다.", figures.facts(self.lg), figures.date_strings(self.lg), set())
        self.assertEqual([(x[2], x[3]) for x in found], [("2026-10-15", "date-ok"), ("2026-10-17", "date-no")])   # month and day are not figures
        # years are not blanket-excluded: a year with a count unit is checked as a count
        self.assertEqual(verdicts("ToyBench: 타저자 2025편", self.lg), [("2025", "no")])
        self.assertEqual(verdicts("ICLR 2026에서 ToyBench 12편", self.lg), [("12", "ok")])

    def test_the_terminal_marks_an_unverified_date_apart_from_figures(self):
        from bench2agent import cli
        out = cli.render({"segs": [{"t": "p", "s": "확인 "}, {"t": "n", "s": "2026-10-17", "v": "no", "kind": "date"}, {"t": "p", "s": "입니다."}]})
        self.assertIn("2026-10-17 [unverified date]", out)


if __name__ == "__main__":
    unittest.main()
