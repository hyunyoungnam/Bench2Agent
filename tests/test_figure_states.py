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




class TerminalMarks(unittest.TestCase):
    def test_only_a_contradiction_reads_as_a_difference(self):
        from bench2agent.cli import render
        turn = {"segs": [{"t": "n", "s": "14", "v": "no"}, {"t": "p", "s": " "}, {"t": "n", "s": "13", "v": "na"},
                         {"t": "p", "s": " "}, {"t": "n", "s": "2026-10-21", "v": "na", "kind": "date"}, {"t": "p", "s": " "},
                         {"t": "n", "s": "5", "v": "ok"}]}
        self.assertEqual(render(turn), "14 [differs from tool result] 13 [unverified figure] 2026-10-21 [unverified date] 5")


if __name__ == "__main__":
    unittest.main()
