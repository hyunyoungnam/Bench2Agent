"""Three figure states (owner/Codex, 2026-10-21): confirmed = the same context in this turn's tool results holds the
value; contradicted = the same context holds a different value; unverified = no matching call or the context cannot
be fixed. Missing evidence is never a contradiction, and an unverified figure is still counted."""
import unittest

from bench2agent.core import chat, figures
from bench2agent.core.verify import Verifier
from bench2agent.runtime.base import check_trail

USAGE = ("benchmark_usage", {"topic": "robotics", "latest": True}, {
    "role": "evaluates_on", "coverage": {"parsed_papers": 237}, "results": [{"id": "libero", "name": "LIBERO", "papers": 13}]})
OTHER = ("benchmark_usage", {"topic": "vision"}, {
    "role": "evaluates_on", "coverage": {"parsed_papers": 500}, "results": [{"id": "libero", "name": "LIBERO", "papers": 13}]})
ANCHOR = '⟦benchmark_usage:{"topic":"robotics","latest":true}|LIBERO 13 of 237 papers⟧'


def log(*entries):
    return figures.TurnLog([{"name": n, "arguments": a, "result": r, "revision": "rev1"} for n, a, r in entries])


def run(text, lg):
    segs, v = chat.segment(text, None, Verifier.__new__(Verifier), [], lg)
    return [(s["s"], s["v"]) for s in segs if s["t"] == "n"], v, segs


class FigureStates(unittest.TestCase):
    def test_a_correct_value_restated_without_a_tool_call_is_unverified_and_counted(self):
        states, v, segs = run("At CoRL 2025: " + ANCHOR, log())
        self.assertEqual(states, [("LIBERO 13 of 237 papers", "na")])
        self.assertEqual((v["fchecked"], v["fpassed"]), (1, 0))
        self.assertEqual([s["why"] for s in segs if s["t"] == "n"], ["no tool was called in this turn"])

    def test_a_wrong_value_without_a_tool_call_is_unverified_never_confirmed(self):
        states, v, _ = run("LIBERO was used in 14 of 237 analyzed papers (5.9%) at CoRL 2025.", log())
        self.assertEqual(sorted({s for _, s in states}), ["na"])
        self.assertEqual(v["fpassed"], 0)
        self.assertEqual(v["fchecked"], len(states))                        # statistics in a no-call answer are counted

    def test_a_matching_call_confirms_and_a_different_value_in_the_same_context_contradicts(self):
        self.assertEqual(run("Robotics: " + ANCHOR, log(USAGE))[0], [("LIBERO 13 of 237 papers", "ok")])
        self.assertEqual(run("Robotics: " + ANCHOR, log(USAGE))[1]["fpassed"], 1)
        states, v, _ = run('Robotics: ⟦benchmark_usage:{"topic":"robotics","latest":true}|LIBERO 14 of 237 papers⟧', log(USAGE))
        self.assertEqual(states, [("LIBERO 14 of 237 papers", "no")])

    def test_the_same_number_from_a_call_with_other_arguments_leaves_the_anchor_unverified(self):
        states, _, segs = run("Robotics: " + ANCHOR, log(OTHER))
        self.assertEqual(states, [("LIBERO 13 of 237 papers", "na")])
        self.assertIn("did not call the tool with these arguments", [s["why"] for s in segs if s["t"] == "n"][0])

    def test_an_anchor_without_a_number_is_plain_text(self):
        states, v, segs = run('⟦benchmark_usage:{"topic":"robotics","latest":true}|LIBERO leads⟧', log(USAGE))
        self.assertEqual(states, [])
        self.assertEqual(v["fchecked"], 0)
        self.assertIn("LIBERO leads", "".join(s.get("s", "") for s in segs))

    def test_an_anchor_and_a_plain_sentence_with_the_same_figure_are_each_checked(self):
        states, v, _ = run("Robotics: " + ANCHOR + ". In plain words, LIBERO appears in 13 of 237 papers.", log(USAGE))
        self.assertEqual(states, [("LIBERO 13 of 237 papers", "ok")])               # prose figures that hold stay plain
        self.assertEqual((v["fchecked"], v["fpassed"]), (3, 3))             # the anchor, then 13 and 237 in prose

    def test_a_missing_or_damaged_log_after_cli_tool_calls_keeps_the_whole_turn_unverifiable(self):
        seen = [{"name": "benchmark_usage"}]
        self.assertEqual(check_trail([], None, seen, "rev1"), "the CLI called tools but the MCP server left no log")
        self.assertEqual(check_trail([], "damaged line 2", seen, "rev1"), "damaged line 2")
        self.assertIsNone(check_trail([], None, [], "rev1"))               # no call at all: checked figure by figure instead

    def test_a_date_missing_from_the_tool_results_is_unverified_not_contradicted(self):
        states, v, _ = run("Checked on 2026-10-21, LIBERO had 13 of 237 papers.", log(USAGE))
        self.assertIn(("2026-10-21", "na"), states)
        self.assertNotIn("no", [s for _, s in states])




class ParenthesesAndTotals(unittest.TestCase):
    SCOPE = ("benchmark_scope", {"query": "robotics"}, {"editions": {
        "iclr-2026": {"total_papers": 5468, "arxiv_matched": 4290, "parsed_papers": 4252},
        "icml-2026": {"total_papers": 6629, "arxiv_matched": 4893, "parsed_papers": 4863}}})
    USAGE = ("benchmark_usage", {"topic": "robotics", "latest": True}, {"role": "evaluates_on", "coverage": {
        "parsed_papers": 350, "editions": {"iclr-2026": {"in_scope_parsed": 183}, "icml-2026": {"in_scope_parsed": 167}}},
        "results": [{"id": "libero", "name": "LIBERO", "papers": 82}]})

    def verdicts(self, text, *entries):
        lg = log(*entries)
        return [(r[2], r[3]) for r in figures.scan_connected(text, figures.facts(lg), figures.date_strings(lg), set())]

    def test_a_count_in_parentheses_without_its_measure_is_unverified_not_contradicted(self):
        # live robotics answer (2026-10-21): "ICLR 2026 (183 papers)" read as a usage count and contradicted
        out = self.verdicts("Only ICLR 2026 (183 papers) and ICML 2026 (167) carry the label.", self.USAGE)
        self.assertNotIn("no", [v for _, v in out])
        self.assertEqual(self.verdicts("ICLR 2026에서 이번 조건으로 분석한 논문은 183편", self.USAGE), [("183", "ok")])
        self.assertEqual(self.verdicts("ICLR 2026에서 이번 조건으로 분석한 논문은 184편", self.USAGE), [("184", "no")])
        self.assertEqual(self.verdicts("LIBERO: 82 of 350 analyzed papers", self.USAGE), [("82", "ok"), ("350", "ok")])
        self.assertEqual(self.verdicts("LIBERO: 81 of 350 analyzed papers", self.USAGE)[0], ("81", "no"))   # still a contradiction

    def test_a_sum_over_editions_connects_only_to_an_explicit_total(self):
        from bench2agent.core.benchmarks import edition_totals
        tot = edition_totals(self.SCOPE[2]["editions"])
        self.assertEqual((tot["total_papers"], tot["arxiv_matched"], tot["parsed_papers"], tot["field_labelled_papers"]),
                         (12097, 9183, 9115, None))                    # a measure missing in an edition has no total
        scope = (self.SCOPE[0], self.SCOPE[1], {**self.SCOPE[2], "edition_totals": tot})
        self.assertEqual(self.verdicts("12,097 listed papers", scope), [("12,097", "ok")])
        self.assertEqual(self.verdicts("9,115 analyzed papers", scope), [("9,115", "ok")])
        self.assertNotIn(("12,098", "ok"), self.verdicts("12,098 listed papers", scope))
        self.assertNotIn(("12,097", "ok"), self.verdicts("12,097 listed papers", self.SCOPE))   # no explicit total: never summed here


class KoreanPairsAndDiagnostics(unittest.TestCase):
    # live terminal answer (Claude Code, 2026-10-21, ICLR CIFAR-10 on 1e1fa171722da693): right numbers marked contradicted
    TREND = ("benchmark_trend", {"benchmark": "cifar-10", "venue": "iclr"}, {"id": "cifar-10", "name": "CIFAR-10", "role": "evaluates_on",
             "coverage": {"papers_with_unattributed_benchmark_mentions": 4185}, "results": [
                 {"edition": "iclr-2024", "papers": 179, "parsed_papers": 1894}, {"edition": "iclr-2025", "papers": 197, "parsed_papers": 3060}]})
    SCOPE = ("benchmark_scope", {}, {"editions": {"iclr-2024": {"total_papers": 2296}, "iclr-2025": {"total_papers": 3827}}})

    def verdicts(self, text):
        lg = log(self.TREND, self.SCOPE)
        return [(r[2], r[3]) for r in figures.scan_connected(text, figures.facts(lg), figures.date_strings(lg), set())]

    def test_a_korean_whole_then_part_pair_is_not_read_as_use_counts(self):
        text = "## CIFAR-10\n분석된 논문 수는 2024년 2,296편 중 1,894편, 2025년 3,827편 중 3,060편입니다."
        out = dict(self.verdicts(text))
        self.assertEqual((out["1,894"], out["3,060"]), ("ok", "ok"))
        self.assertNotIn("no", (out["2,296"], out["3,827"]))
        self.assertEqual(dict(self.verdicts("## CIFAR-10\n2025년 3,827편 중 3,061편입니다."))["3,061"], "no")   # wrong part: contradicted
        self.assertEqual(dict(self.verdicts("## CIFAR-10\n2025년 3,827편 중 197편이 평가에 썼습니다."))["197"], "ok")

    def test_an_edition_word_does_not_make_an_item_count_an_edition_count(self):
        out = dict(self.verdicts("세 에디션을 합쳐 4,185건의 논문-에디션 관측에 해결되지 않은 벤치마크 언급이 있습니다."))
        self.assertEqual(out["4,185"], "ok")


class PooledAndDiagnosticWords(unittest.TestCase):
    # live browser answer (Codex, 2026-10-21, robotics on 1e1fa171722da693): right numbers marked contradicted
    USAGE = ("benchmark_usage", {"topic": "robotics", "latest": True}, {"role": "evaluates_on", "coverage": {
        "parsed_papers": 513, "papers_with_unattributed_benchmark_mentions": 221,
        "editions": {"iclr-2026": {"parsed_papers": 4252, "in_scope_parsed": 183}, "icml-2026": {"parsed_papers": 4863, "in_scope_parsed": 167},
                     "neurips-2025": {"parsed_papers": 4560, "in_scope_parsed": 163}}},
        "results": [{"id": "libero", "name": "LIBERO", "papers": 82}]})

    def verdicts(self, text):
        lg = log(self.USAGE)
        return dict((r[2], r[3]) for r in figures.scan_connected(text, figures.facts(lg), figures.date_strings(lg), set()))

    def test_a_pooled_value_over_named_editions_connects_and_a_wrong_one_is_unverified(self):
        text = "The robotics filter yields {} parsed papers across ICLR 2026, ICML 2026 and NeurIPS 2025."
        self.assertEqual(self.verdicts(text.format(513))["513"], "ok")
        self.assertEqual(self.verdicts(text.format(514))["514"], "na")
        self.assertEqual(self.verdicts("ICLR 2026 has 184 parsed papers in scope.")["184"], "no")      # one named edition: still contradicted

    def test_a_row_denominator_is_the_usage_calls_pooled_one_even_when_evidence_calls_carry_their_own(self):
        evidence = ("benchmark_evidence", {"benchmark": "libero", "role": "evaluates_on"}, {"id": "libero", "name": "LIBERO", "role": "evaluates_on",
                    "coverage": {"parsed_papers": 4252, "editions": {"iclr-2026": {"parsed_papers": 4252}}}, "results": []})
        lg = log(self.USAGE, evidence)
        out = lambda text: dict((r[2], r[3]) for r in figures.scan_connected(text, figures.facts(lg), figures.date_strings(lg), set()))
        row = "| Benchmark | Papers evaluating on it |\n|---|---|\n| LIBERO | 82 of {} papers (16.0%) |\n"
        self.assertEqual(out(row.format(513))["513"], "ok")
        self.assertEqual(out(row.format(514))["514"], "no")              # only the usage call fits "evaluating": still contradicted
        plain = "LIBERO: 82 of {} papers."                                  # the evidence lookup counts no use: its denominator is not this pair's
        self.assertEqual(out(plain.format(513))["513"], "ok")
        self.assertEqual(out(plain.format(514))["514"], "no")
        self.assertEqual(out(plain.format(4252))["4252"], "no")

    def test_papers_with_unresolved_mentions_are_the_scope_diagnostic(self):
        self.assertEqual(self.verdicts("There are also 221 papers with unresolved benchmark mentions.")["221"], "ok")
        self.assertNotEqual(self.verdicts("LIBERO: 82 of 513 papers. There are also 222 papers with unresolved benchmark mentions.")["222"], "ok")


class Review061(unittest.TestCase):
    """Codex's review of the 0.6.1 candidate (2026-10-22): four false confirmations, each beside the correct
    statement that must still connect. A value is confirmed only by a fact of the same lookup and scope."""
    USAGE = PooledAndDiagnosticWords.USAGE
    EVIDENCE = ("benchmark_evidence", {"benchmark": "libero", "role": "evaluates_on"}, {
        "id": "libero", "name": "LIBERO", "role": "evaluates_on",
        "coverage": {"parsed_papers": 4252, "editions": {"iclr-2026": {"parsed_papers": 4252}}}, "results": []})
    ICLR = ("benchmark_usage", {"edition": "iclr-2026"}, {"role": "evaluates_on", "coverage": {"parsed_papers": 4252, "editions": {
        "iclr-2026": {"total_papers": 5468, "parsed_papers": 4252, "in_scope_parsed": 4252}}},
        "results": [{"id": "libero", "name": "LIBERO", "papers": 82}]})
    SCOPE = ("benchmark_scope", {}, {"editions": {f"iclr-{y}": {"parsed_papers": 80} for y in range(1965, 2026)}})
    NEW = ("new_benchmarks", {}, {"results": [{"id": "toybench", "name": "ToyBench", "adoption": {"evaluates_on": {
        "by_edition": {f"iclr-{y}": {"others": 1} for y in (2024, 2025, 2026)}}}}]})
    TWO = ("benchmark_usage", {"topic": "robotics", "venue": "iclr"}, {"role": "evaluates_on", "coverage": {
        "parsed_papers": 300, "editions": {"iclr-2025": {"in_scope_parsed": 120}, "iclr-2026": {"in_scope_parsed": 180}}}, "results": []})

    def verdicts(self, text, *entries):
        lg = log(*entries)
        return dict((r[2], r[3]) for r in figures.scan_connected(text, figures.facts(lg), figures.date_strings(lg), set()))

    def test_a_pooled_value_fits_only_the_editions_it_was_pooled_over(self):
        self.assertNotEqual(self.verdicts("ICLR 2026 and ICML 2026 together have 513 parsed papers.", self.USAGE)["513"], "ok")
        self.assertNotEqual(self.verdicts("LIBERO was used by 82 papers in ICLR 2026 and ICML 2026.", self.USAGE)["82"], "ok")
        # the whole list, after the number past the 30-character clause or before it across commas, connects
        self.assertEqual(self.verdicts("The robotics filter yields 513 parsed papers across ICLR 2026, ICML 2026 and NeurIPS 2025.", self.USAGE)["513"], "ok")
        self.assertEqual(self.verdicts("ICLR 2026, ICML 2026 and NeurIPS 2025 together have 513 parsed papers.", self.USAGE)["513"], "ok")
        # editions with no in-scope papers add nothing: a filter over 11 latest editions, labeled in three
        wide = (self.USAGE[0], self.USAGE[1], {**self.USAGE[2], "coverage": {**self.USAGE[2]["coverage"], "editions": {
            **self.USAGE[2]["coverage"]["editions"], "aaai-2026": {"parsed_papers": 2823, "in_scope_parsed": 0}}}})
        self.assertEqual(self.verdicts("The robotics filter yields 513 parsed papers across ICLR 2026, ICML 2026 and NeurIPS 2025.", wide)["513"], "ok")
        self.assertNotEqual(self.verdicts("ICLR 2026 and ICML 2026 together have 513 parsed papers.", wide)["513"], "ok")
        # a single-edition lookup's pooled value is that edition's
        self.assertEqual(self.verdicts("ICLR 2026에서 LIBERO로 평가한 논문은 82편입니다.", self.ICLR)["82"], "ok")

    def test_an_edition_list_is_read_only_as_far_as_its_own_claim(self):
        out = self.verdicts("ICLR 2026과 ICML 2026에서 분석한 논문은 합쳐서 350편이고, NeurIPS 2025에서 분석한 논문은 163편입니다.", self.USAGE)
        self.assertEqual((out["163"], out["350"] != "ok"), ("ok", True))
        out = self.verdicts("There are 513 parsed papers in ICLR 2026 and ICML 2026, and 163 in NeurIPS 2025.", self.USAGE)
        self.assertNotEqual(out["513"], "ok")
        text = "ICLR 2026 has 183 analyzed papers in scope and ICML 2026 has {} analyzed papers in scope."
        self.assertEqual(self.verdicts(text.format(167), self.USAGE)["167"], "ok")
        self.assertEqual(self.verdicts(text.format(183), self.USAGE), {"183": "no"})   # ICLR 2026 is the first number's

    def test_a_stated_measure_decides_and_an_unstated_one_is_not_guessed(self):
        out = self.verdicts("ICLR 2026 수록 논문 4252편 중 82편이 LIBERO로 평가했습니다.", self.ICLR)
        self.assertEqual((out["4252"], out["82"]), ("no", "ok"))                 # 수록 = listed: 5,468
        self.assertEqual(self.verdicts("ICLR 2026 수록 논문 5468편 중 82편이 LIBERO로 평가했습니다.", self.ICLR)["5468"], "ok")
        self.assertEqual(self.verdicts("ICLR 2026 분석 논문 4,252편 중 82편이 LIBERO로 평가했습니다.", self.ICLR)["4,252"], "ok")
        self.assertEqual(self.verdicts("ICLR 2026 4,252편 중 82편이 LIBERO로 평가했습니다.", self.ICLR)["4,252"], "ok")   # a use count's whole is analyzed
        # "학회 전체" says where, not what: the measure word that follows decides; none leaves a total unverified
        self.assertEqual(self.verdicts("ICLR 2026 학회 전체에서 분석한 논문은 4,252편입니다.", self.ICLR)["4,252"], "ok")
        self.assertEqual(self.verdicts("ICLR 2026 학회 전체에서 LIBERO로 평가한 논문은 82편입니다.", self.ICLR)["82"], "ok")
        self.assertEqual(self.verdicts("ICLR 2026 학회 전체에서 LIBERO로 평가한 논문은 83편입니다.", self.ICLR)["83"], "no")
        self.assertEqual(self.verdicts("ICLR 2026 학회 전체 논문은 4,252편입니다.", self.ICLR)["4,252"], "na")
        # a part that connects nowhere leaves its implied whole unverified
        self.assertEqual(self.verdicts("ICLR 2026 4,252편 중 타 저자 7편이 LIBERO를 썼습니다.", self.ICLR)["4,252"], "na")   # no adoption result
        self.assertEqual(self.verdicts("ICLR 2026 4,252편 중 7편이 LIBERO로 평가했습니다.", self.ICLR)["7"], "no")   # a wrong part keeps its context

    def test_a_ratio_connects_inside_one_lookup(self):
        row = "| Benchmark | Papers evaluating on it |\n|---|---|\n| LIBERO | 82 of {} papers ({}%) |\n"
        out = self.verdicts(row.format(4252, "16.0"), self.USAGE, self.EVIDENCE)
        self.assertEqual((out["82"], out["4252"], out["16.0"]), ("ok", "no", "ok"))
        out = self.verdicts(row.format(513, "16.0"), self.USAGE, self.EVIDENCE)
        self.assertEqual((out["82"], out["513"], out["16.0"]), ("ok", "ok", "ok"))
        # both lookups return 13, over 237 and over 500: the whole and the share must come from the same one
        out = self.verdicts("LIBERO: 13 of 500 papers (2.6%).", USAGE, OTHER)
        self.assertEqual((out["13"], out["500"], out["2.6"]), ("ok", "ok", "ok"))
        self.assertNotEqual(self.verdicts("LIBERO: 13 of 237 papers (2.6%).", USAGE, OTHER)["2.6"], "ok")
        self.assertNotEqual(self.verdicts("LIBERO: 13 of 500 papers (5.5%).", USAGE, OTHER)["5.5"], "ok")
        self.assertEqual(self.verdicts("LIBERO: 500편 중 13편(2.6%)", USAGE, OTHER)["2.6"], "ok")

    def test_installed_data_query_scope_and_a_benchmarks_editions_stay_apart(self):
        self.assertEqual(self.verdicts("The installed data covers 3 editions.", self.SCOPE, self.NEW)["3"], "no")
        self.assertEqual(self.verdicts("The installed data covers 61 editions.", self.SCOPE, self.NEW)["61"], "ok")
        self.assertEqual(self.verdicts("The data covers 63 editions.", self.SCOPE, self.NEW)["63"], "no")    # a real error stays one
        self.assertEqual(self.verdicts("ToyBench was adopted in 3 editions.", self.SCOPE, self.NEW)["3"], "ok")
        self.assertEqual(self.verdicts("설치 데이터는 2개 회차입니다.", self.SCOPE, self.TWO)["2"], "no")
        self.assertEqual(self.verdicts("이번 조회는 2개 회차입니다.", self.SCOPE, self.TWO)["2"], "ok")
        self.assertEqual(self.verdicts("설치 데이터는 61개 회차입니다.", self.SCOPE, self.TWO)["61"], "ok")


    def test_a_restated_figure_and_a_range_are_not_contradicted(self):
        # saved robotics answers (2026-10-21): "Of those 513 papers" read as LIBERO's use count; "60–90%" as editions
        lg = (self.USAGE[0], self.USAGE[1], {**self.USAGE[2], "results": [{"id": "libero", "name": "LIBERO", "papers": 82}]})
        out = self.verdicts("LIBERO 82 of 513 parsed papers. Of those 513 papers, 221 have an unresolved mention.", lg)
        self.assertEqual(out["513"], "ok")
        self.assertEqual(self.verdicts("LIBERO 82 of 513 parsed papers. Of those 514 papers, few say so.", lg)["514"], "na")
        out = self.verdicts("Each edition covers roughly 60–90% of that venue's papers.", self.SCOPE)
        self.assertEqual((out["60"], out["90"]), ("na", "na"))
        self.assertEqual(list(self.verdicts("ICLR 2024–2026 covers 3 editions.", self.SCOPE)), ["3"])   # a year span is not a range of values


class TerminalMarks(unittest.TestCase):
    def test_only_a_contradiction_reads_as_a_difference(self):
        from bench2agent.cli import render
        turn = {"segs": [{"t": "n", "s": "14", "v": "no"}, {"t": "p", "s": " "}, {"t": "n", "s": "13", "v": "na"},
                         {"t": "p", "s": " "}, {"t": "n", "s": "2026-10-21", "v": "na", "kind": "date"}, {"t": "p", "s": " "},
                         {"t": "n", "s": "5", "v": "ok"}]}
        self.assertEqual(render(turn), "14 [differs from tool result] 13 [unverified figure] 2026-10-21 [unverified date] 5")


if __name__ == "__main__":
    unittest.main()
