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


class TerminalMarks(unittest.TestCase):
    def test_only_a_contradiction_reads_as_a_difference(self):
        from bench2agent.cli import render
        turn = {"segs": [{"t": "n", "s": "14", "v": "no"}, {"t": "p", "s": " "}, {"t": "n", "s": "13", "v": "na"},
                         {"t": "p", "s": " "}, {"t": "n", "s": "2026-10-21", "v": "na", "kind": "date"}, {"t": "p", "s": " "},
                         {"t": "n", "s": "5", "v": "ok"}]}
        self.assertEqual(render(turn), "14 [differs from tool result] 13 [unverified figure] 2026-10-21 [unverified date] 5")


if __name__ == "__main__":
    unittest.main()
