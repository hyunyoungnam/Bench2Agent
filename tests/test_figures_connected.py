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
    return [(raw, v) for _a, _b, raw, v, _w in figures.scan_connected(text, figures.facts(lg), figures.date_strings(lg), set(),
                                                                      figures.verbatim_strings(lg))]


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

    def test_unattributed_coverage_is_neither_missing_labels_nor_non_use(self):
        # Reproduced on the robotics response (513 observations, 221 with
        # unresolved mentions). The old checker accepted "unlabelled 221".
        # Old saved tool logs contain only the legacy field name.
        for metrics in ({"unattributed_papers": 221},
                        {"unattributed_papers": 221, "papers_with_unattributed_benchmark_mentions": 221}):
            lg = log(("benchmark_usage", {"role": "evaluates_on"}, {
                "role": "evaluates_on", "coverage": {
                    "parsed_papers": 513, "field_labelled_papers": 513,
                    "unlabelled_papers": 0, **metrics}, "results": []}))
            for claim in ("미라벨 논문 221편", "Unlabelled papers: 221", "분모 221편",
                          "221 papers state no benchmark", "벤치마크 사용 미명시 논문 221편"):
                self.assertNotEqual(verdicts(claim, lg), [("221", "ok")], claim)
            self.assertEqual(verdicts("Unlabelled papers: 0", lg), [("0", "ok")])
            self.assertEqual(verdicts("미귀속 논문 221편", lg), [("221", "ok")])
            self.assertEqual(verdicts("unattributed_papers: 221", lg), [("221", "ok")])
            # This diagnostic spans all roles even in an evaluation query.
            self.assertNotEqual(verdicts("평가 미귀속 논문 221편", lg), [("221", "ok")])

    def test_scope_unattributed_observations_do_not_verify_benchmark_adoption(self):
        lg = log(("benchmark_adoption", {"benchmark": "toybench"}, {
            "id": "toybench", "name": "ToyBench", "unattributed_using_papers": 18,
            "coverage": {"unattributed_papers": 221,
                         "papers_with_unattributed_benchmark_mentions": 221}}))
        self.assertEqual(verdicts("ToyBench: 미귀속 18편", lg), [("18", "ok")])
        self.assertEqual(verdicts("ToyBench: 미귀속 221편", lg), [("221", "no")])
        self.assertEqual(verdicts("미귀속 논문 221편", lg), [("221", "na")])  # unit is ambiguous

    def test_a_paper_links_observation_is_its_text_its_status_code_and_dates_are_not_figures(self):
        # Reproduced on the live answers of 2026-10-21 (ToxiAlert-Bench, D-GARA-152, HybridMatch): the
        # observation copied word for word, "HTTP 404" and "10-21" were marked as unverified figures.
        seen = "repository reached on 2026-10-21 UTC; file list of branch main read: 96 files; README read"
        lg = log(("benchmark_adoption", {"benchmark": "hybridmatch"}, {
            "id": "hybridmatch", "name": "HybridMatch", "locations": [],
            "introducing_paper": {"paper_id": "2503.07019", "links": [{
                "paper_id": "2503.07019", "url": "https://github.com/hxwork/HybridReg˙PyTorch",
                "observed": "could not be reached or confirmed on 2026-10-21 UTC (HTTP 404)", "checked_at": "2026-10-21T00:00:43Z",
                "corrected": {"url": "https://github.com/hxwork/HybridReg_PyTorch", "observed": seen, "n_files": 96}}]}}))
        self.assertEqual(verdicts(f'HybridMatch: 보정한 주소의 관측 "{seen}"', lg), [("2026-10-21", "date-ok")])
        self.assertEqual(verdicts("HybridMatch의 인쇄된 주소는 HTTP 404였습니다", lg), [])
        self.assertEqual(verdicts("HybridMatch: 확인 시각(10-21)", lg), [("10-21", "date-ok")])
        self.assertEqual(verdicts("HybridMatch: 보정한 주소에서 파일 96개를 읽었습니다", lg), [("96", "ok")])
        self.assertEqual(verdicts("HybridMatch: the corrected address lists 96 files", lg), [("96", "ok")])
        self.assertEqual(verdicts("HybridMatch: the corrected address lists 95 files", lg), [("95", "no")])
        self.assertEqual(verdicts("HybridMatch: 확인 시각(11-30)", lg), [("11", "na"), ("-30", "na")])   # not a returned date

    def test_an_edition_table_whose_figure_header_mentions_the_benchmark_is_read_by_edition(self):
        # Reproduced on a live Codex answer (2026-10-21): "Papers evaluating on the benchmark" was taken for the
        # identity column, so every figure in the ICLR CIFAR-10 table was left unverified.
        self.lg = log(SCOPE, NEW, ("benchmark_trend", {"benchmark": "toybench"}, {**TREND[2], "role": "evaluates_on"}))   # the tool states the role
        text = ("ToyBench at ICLR:\n\n| Analysed edition | Papers evaluating on the benchmark | Parsed papers |\n|---|---:|---:|\n"
                "| ICLR 2025 | 10 | 3,059 |\n| ICLR 2026 | 12 | 4,252 |\n")
        self.assertEqual(verdicts(text, self.lg), [("10", "ok"), ("3,059", "ok"), ("12", "ok"), ("4,252", "ok")])
        wrong = text.replace("| ICLR 2026 | 12 |", "| ICLR 2026 | 10 |")
        self.assertIn(("10", "no"), verdicts(wrong, self.lg))
        # the same answer's rate column: "per 1,000 parsed" is one rate phrase, not the denominator
        lg = log(("benchmark_trend", {"benchmark": "toybench"}, {"id": "toybench", "name": "ToyBench", "role": "evaluates_on", "results": [
            {"edition": "iclr-2026", "papers": 12, "parsed_papers": 4252, "per_1000": 2.822}]}))
        rate = "ToyBench:\n\n| Analysed edition | Papers evaluating on the benchmark | Evaluation papers per 1,000 parsed |\n|---|---:|---:|\n| ICLR 2026 | 12 | 2.822 |\n"
        self.assertEqual(verdicts(rate, lg), [("12", "ok"), ("2.822", "ok")])

    def test_a_benchmark_column_whose_header_mentions_its_paper_is_still_the_identity(self):
        # the live Codex answer that followed (2026-10-21): "Benchmark — location; introducing paper"
        text = ("| Benchmark — location; introducing paper | Evaluation papers: other / self / undetermined authors; unresolved using papers |\n|---|---|\n"
                "| **ToyBench** — no confirmed location | 5 other / 0 self / 0 undetermined; 0 unresolved |\n")
        self.assertEqual(verdicts(text, self.lg)[:3], [("5", "ok"), ("0", "ok"), ("0", "ok")])
        self.assertEqual(verdicts(text.replace("5 other", "7 other"), self.lg)[0], ("7", "no"))   # 7 is ToyBench's training use

    def test_a_pair_inside_a_table_cell_reads_its_denominator_as_one(self):
        # the live robotics answer (2026-10-21): "| LIBERO | 82 of 513 (159.844) |" under a header naming the
        # rate; the 513 was judged as a rate and contradicted in every row
        lg = log(("benchmark_usage", {"topic": "robotics"}, {"role": "evaluates_on", "coverage": {"parsed_papers": 513}, "results": [
            {"id": "libero", "name": "LIBERO", "papers": 82, "per_1000": 159.844}]}))
        text = "| Benchmark | Papers evaluating on it, per 1,000 |\n|---|---|\n| LIBERO | 82 of 513 (159.844) |\n"
        self.assertEqual(verdicts(text, lg), [("82", "ok"), ("513", "ok"), ("159.844", "ok")])
        self.assertEqual(verdicts(text.replace("82 of 513", "82 of 514"), lg)[1], ("514", "no"))

    def test_a_section_heading_names_the_benchmark(self):
        text = "### OtherBench\n- 타저자 3편, 자기 2편\n### ToyBench\n- 타저자 3편"
        self.assertEqual(verdicts(text, self.lg), [("3", "ok"), ("2", "ok"), ("3", "no")])

    def test_an_edition_series_reads_count_over_denominator(self):
        text = "### ToyBench\nICLR 추이: iclr-2025 10/3059, iclr-2026 12/4252"
        self.assertEqual(verdicts(text, self.lg), [("10", "ok"), ("3059", "ok"), ("12", "ok"), ("4252", "ok")])
        self.assertEqual(verdicts("### ToyBench\niclr-2025 12/3059", self.lg), [("12", "no"), ("3059", "ok")])

    def test_a_name_shared_by_two_ids_verifies_nothing_and_an_id_does(self):
        lg = log(("benchmark_trend", {"benchmark": "noisy-mnist"}, {"id": "noisy-mnist", "name": "Noisy-MNIST",
                  "results": [{"edition": "iclr-2025", "papers": 0, "parsed_papers": 3060}]}),
                 ("benchmark_adoption", {"benchmark": "noisy-mnist-2"}, {"id": "noisy-mnist-2", "name": "Noisy MNIST", "before_claim": 0}))
        # "Noisy MNIST" and "Noisy-MNIST" are one spelling (spaces and hyphens fold) but two ids: not attributable
        self.assertEqual(verdicts("## Noisy MNIST\n- iclr-2025 0/3060", lg), [("0", "na"), ("3060", "na")])
        # the id names one benchmark ("noisy-mnist" is not read inside "noisy-mnist-2")
        self.assertEqual(verdicts("## noisy-mnist\n- iclr-2025 0/3060", lg), [("0", "ok"), ("3060", "ok")])
        self.assertEqual(verdicts("noisy-mnist-2: before_claim 0", lg), [("0", "ok")])

    # ---- reproduced false passes (Codex, 2026-10-16) and the limits agreed with them
    def test_a_scope_wide_count_never_stands_in_for_a_named_benchmark(self):
        self.assertEqual(verdicts("ToyBench는 ICLR 2025에서 80편", self.lg), [("80", "no")])      # 80 is ICLR 2025's parsed papers, ToyBench used 10
        self.assertEqual(verdicts("ToyBench ICLR 2025 분모 80편", self.lg), [("80", "no")])       # nor as its denominator: ToyBench's is 3,059
        self.assertEqual(verdicts("ToyBench ICLR 2025 분모 3059편", self.lg), [("3059", "ok")])   # the denominator of ToyBench's own query

    def test_humaneval_and_humaneval_plus_stay_apart(self):
        lg = log(("new_benchmarks", {}, {"results": [
            {"id": "humaneval", "name": "HumanEval", "adoption": {"evaluates_on": {"others": 10, "self": 0, "undetermined": 0}}},
            {"id": "humaneval-plus", "name": "HumanEval+", "adoption": {"evaluates_on": {"others": 20, "self": 0, "undetermined": 0}}}]}))
        self.assertEqual(verdicts("HumanEval: 20 papers by other authors", lg), [("20", "no")])
        self.assertEqual(verdicts("HumanEval: 10 papers by other authors", lg), [("10", "ok")])
        self.assertEqual(verdicts("HumanEval+: 20 papers by other authors", lg), [("20", "ok")])

    def test_a_homonym_name_is_unverified_even_when_the_values_agree(self):
        lg = log(("new_benchmarks", {}, {"results": [
            {"id": "sine", "name": "Sine", "adoption": {"evaluates_on": {"others": 0, "self": 0, "undetermined": 0}, "unattributed_using_papers": 18}},
            {"id": "sine-2", "name": "Sine", "adoption": {"evaluates_on": {"others": 0, "self": 0, "undetermined": 0}, "unattributed_using_papers": 18}}]}))
        self.assertEqual(verdicts("Sine: 미귀속 18편", lg), [("18", "na")])
        self.assertEqual(verdicts("sine-2: 미귀속 18편", lg), [("18", "ok")])

    def test_a_table_cell_takes_its_row_benchmark_and_its_column_header_only(self):
        table = "| Benchmark | Other authors |\n|---|---|\n| ToyBench  | 3 papers     |\n| OtherBench | 3 papers |"
        self.assertEqual(verdicts(table, self.lg), [("3", "no"), ("3", "ok")])
        numbered = "| # | Benchmark | Other authors |\n|---|---|---|\n| 1 | OtherBench | 3 papers |"
        self.assertEqual(verdicts(numbered, self.lg), [("1", "na"), ("3", "ok")])       # the ordinal column is no identity
        unreadable = "| Metric | Value |\n|---|---|\n| Other authors | 3 papers |"
        self.assertEqual(verdicts(unreadable, self.lg), [("3", "na")])

    def test_usage_adoption_and_denominators_are_never_one_pool(self):
        self.assertEqual(verdicts("ToyBench는 ICLR 2025에서 3059편이 사용", self.lg), [("3059", "no")])   # a denominator is not a use count
        self.assertEqual(verdicts("ToyBench 12편", self.lg), [("12", "na")])                         # usage or adoption not stated
        self.assertEqual(verdicts("ToyBench ICLR 2026 사용 12편", self.lg), [("12", "ok")])           # usage, per edition
        self.assertEqual(verdicts("ToyBench 평가 타저자 12편", self.lg), [("12", "no")])               # adoption: 5 unique papers

    def test_dates_are_not_statistics_and_are_never_counted_as_verified_figures(self):
        found = figures.scan_connected("생성 2026-10-15입니다. 확인 2026-10-17입니다.", figures.facts(self.lg), figures.date_strings(self.lg), set())
        self.assertEqual([(x[2], x[3]) for x in found], [("2026-10-15", "date-ok"), ("2026-10-17", "date-no")])   # month and day are not figures
        # years are not blanket-excluded: a year with a count unit is checked as a count
        self.assertEqual(verdicts("ToyBench: 타저자 2025편", self.lg), [("2025", "no")])
        self.assertEqual(verdicts("ICLR 2026에서 ToyBench 12편", self.lg), [("12", "ok")])

    def test_the_policy_example_form_n_of_m_parsed_papers_is_read_like_n_slash_m(self):
        # 2026-10-19: the policy's own example "LIBERO 82 of 513 parsed papers" had no cue for either number
        self.assertEqual(verdicts("### ToyBench\niclr-2025 10 of 3059 parsed papers", self.lg), [("10", "ok"), ("3059", "ok")])
        self.assertEqual(verdicts("### ToyBench\niclr-2025 12 of 3059 parsed papers", self.lg), [("12", "no"), ("3059", "ok")])
        self.assertEqual(verdicts("ToyBench: one of 12 papers", self.lg)[0][1], "na")        # "of" after a word, not a number
        # an edition's parsed count right before "파싱/parsed" is its denominator, not a use count ("에디션" names the edition)
        self.assertEqual(verdicts("### ToyBench\niclr-2026 에디션은 4,252편 파싱되었습니다.", self.lg), [("4,252", "ok")])
        self.assertEqual(verdicts("### ToyBench\niclr-2026 에디션은 4,000편 파싱되었습니다.", self.lg), [("4,000", "no")])

    def test_an_edition_that_leads_the_next_number_is_not_this_numbers_context(self):
        # 2026-10-19 MM-NIAH: "…before_claim 0 에디션별로는 neurips-2025에 타 저자 3편" (no full stop after the
        # anchor) read neurips-2025 as the 0's edition; it belongs to the 3
        self.assertEqual(verdicts("### ToyBench\n평가 타저자 5편 에디션별로는 iclr-2026에 12편", self.lg)[0], ("5", "ok"))
        # "0 에디션별로는" is "0, by edition", not "0 editions"
        self.assertEqual(verdicts("### ToyBench\n학습 타저자 7 에디션별로는 iclr-2026에 12편", self.lg)[0], ("7", "ok"))
        # a trailing edition still qualifies its number: no false pass for "10/4252 iclr-2026" (iclr-2026 is 12)
        self.assertEqual(verdicts("### ToyBench\n- 사용 10/4252 iclr-2026", self.lg), [("10", "no"), ("4252", "ok")])
        self.assertEqual(verdicts("### ToyBench\n- 사용 10/4252 iclr-2025", self.lg), [("10", "ok"), ("4252", "no")])
        # the count of "N of M parsed papers in <edition>" shares the edition after its denominator
        self.assertEqual(verdicts("### ToyBench\n- 10 of 4252 parsed papers in iclr-2026", self.lg), [("10", "no"), ("4252", "ok")])
        self.assertEqual(verdicts("### ToyBench\n- 12 of 4252 parsed papers in iclr-2026", self.lg), [("12", "ok"), ("4252", "ok")])
        self.assertNotEqual(verdicts("### ToyBench\n사용 10편 iclr-2026", self.lg), [("10", "ok")])
        from bench2agent.core import chat
        from bench2agent.core.verify import Verifier
        segs, v = chat.segment('⟦new_benchmarks:{}|ToyBench 평가 타저자 5편⟧ 에디션별로는 iclr-2026에 12편', None, Verifier.__new__(Verifier), [], self.lg)
        self.assertEqual([s["v"] for s in segs if s["t"] == "n"][0], "ok")

    def test_a_word_right_before_a_pair_and_a_rate_header_say_what_is_counted(self):
        # 2026-10-19 README CIFAR-10 answer: "parsed 3059 of 3830 accepted" was read as a use count over a
        # denominator, and a "Per 1000 parsed papers" column as a denominator
        self.assertEqual(verdicts("### ToyBench\nICLR 2025 parsed 3059 of 3830 accepted", self.lg)[0], ("3059", "ok"))
        self.assertEqual(verdicts("### ToyBench\nICLR 2025 parsed 3000 of 3830 accepted", self.lg)[0], ("3000", "no"))
        lg = log(SCOPE, NEW, ("benchmark_trend", {"benchmark": "toybench"}, {"id": "toybench", "name": "ToyBench", "results": [
            {"edition": "iclr-2025", "papers": 10, "parsed_papers": 3059, "per_1000": 3.269}]}))
        table = "| Edition | Papers | Per 1000 parsed papers |\n|---|---|---|\n| iclr-2025 | 10 | 3.27 |"
        self.assertEqual(verdicts("### ToyBench\n" + table, lg)[1], ("3.27", "ok"))
        self.assertEqual(verdicts("### ToyBench\n" + table.replace("3.27", "3.59"), lg)[1], ("3.59", "no"))

    def test_an_anchor_with_no_number_is_plain_text_not_an_unverified_figure(self):
        from bench2agent.core import chat
        from bench2agent.core.verify import Verifier
        from bench2agent import cli
        segs, v = chat.segment('See ⟦new_benchmarks:{}|the figures⟧ for details.', None, Verifier.__new__(Verifier), [], self.lg)
        out = cli.render({"segs": segs, "verified": v})
        self.assertIn("the figures", out)
        self.assertNotIn("[unverified figure]", out)
        self.assertEqual(v["fchecked"], 0)

    def test_an_anchored_table_cell_is_read_with_its_row_and_column(self):
        # 2026-10-19: the model anchored each table cell; the cell alone ("5") says nothing, its row and header do
        from bench2agent.core import chat
        from bench2agent.core.verify import Verifier
        table = ("| Benchmark | Other authors (evaluation) |\n|---|---|\n"
                 '| ToyBench | ⟦new_benchmarks:{}|5⟧ |\n| OtherBench | ⟦new_benchmarks:{}|5⟧ |')
        segs, v = chat.segment(table, None, Verifier.__new__(Verifier), [], self.lg)
        self.assertEqual([s["v"] for s in segs if s["t"] == "n"], ["ok", "no"])          # ToyBench 5 holds; OtherBench's is 3
        self.assertEqual((v["fchecked"], v["fpassed"]), (2, 1))

    def test_the_terminal_marks_an_unverified_date_apart_from_figures(self):
        from bench2agent import cli
        out = cli.render({"segs": [{"t": "p", "s": "확인 "}, {"t": "n", "s": "2026-10-17", "v": "no", "kind": "date"}, {"t": "p", "s": "입니다."}]})
        self.assertIn("2026-10-17 [unverified date]", out)


if __name__ == "__main__":
    unittest.main()
