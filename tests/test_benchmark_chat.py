"""Representative benchmark questions on contrasting, synthetic paper evidence."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bellwether import chat, figures, mcp
from bellwether.benchmarks import BenchmarkStore, build_snapshot


class BenchmarkQuestions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.write("config/benchmarks.json", {"benchmarks": [
            {"id": "toy", "name": "Toy"}, {"id": "mirage-old", "name": "Mirage"}]})
        def intro(bid, name, pid, title, **extra):
            return {"id": bid, "name": name, "fold": name.lower(), "kind": "benchmark",
                    "first_claim": "icml-2025", "developers": ["Alice Smith"],
                    "claims": [{"arxiv_base": pid, "edition": "icml-2025", "title": title,
                                "evidence": "We introduce " + name + " for evaluating robotic policies."}], **extra}
        self.write("config/benchmarks_introduced.json", {"benchmarks": [
            intro("fresh", "Fresh", "2501.00001", "A fresh evaluation protocol"),
            intro("unused", "Unused", "2501.00001", "A fresh evaluation protocol"),
            intro("mirage-new", "Mirage", "2501.00005", "Mirage: evaluating robotic policies",
                  homonym_of_registered="mirage-old")]})
        def use(key, role="evaluates_on", **extra):
            return {key: {"roles": {role: [{"where": "experiments", "text":
                         "We evaluate our policies on the " + key + " benchmark."}]}, **extra}}
        old = [
            ("2401.00001", ["Bob Lee"], use("toy")),
            ("2401.00002", ["Bob Lee"], use("mirage-old")),
            ("2401.00003", ["Bob Lee"], {}),
            ("2401.00004", ["Bob Lee"], use("fresh"))]
        both = use("fresh")
        both["fresh"]["roles"].update(use("fresh", "trains_on")["fresh"]["roles"])
        new = [
            ("2501.00001", ["Alice Smith"], use("fresh")),
            ("2501.00002", ["Bob Lee"], both),
            ("2501.00003", ["Alice Smith"], use("fresh")),
            ("2501.00004", [], use("fresh")),
            ("2501.00005", ["Alice Smith"], use("mirage-old", role_cites=[
                "Mirage: evaluating robotic policies. Alice Smith et al."])),
            ("2501.00006", ["Bob Lee"], use("toy")),
            ("2501.00007", ["Bob Lee"], {})]
        for year, records in ((2024, old), (2025, new)):
            ed = f"icml-{year}"
            self.write(f"data/processed/papers_{year}.jsonl", [
                {"event_id": i, "title": "Paper " + pid, "authors": authors, "subarea": "robotics"}
                for i, (pid, authors, _) in enumerate(records)], lines=True)
            self.write(f"data/raw/arxiv/resolved_{ed}.jsonl", [
                {"arxiv_base": pid, "event_id": i} for i, (pid, _, _) in enumerate(records)], lines=True)
            self.write(f"data/interim/mentions/mentions_{ed}.jsonl", [
                {"arxiv_base": pid, "mentions": uses} for pid, _, uses in records], lines=True)
        self.store = BenchmarkStore(self.root)
        build_snapshot(self.root)

    def write(self, name, obj, lines=False):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(map(json.dumps, obj)) if lines else json.dumps(obj))

    def test_what_is_used_now_with_denominator(self):
        result = self.store.usage({"topic": "robotics"})
        self.assertEqual(list(result["coverage"]["editions"]), ["icml-2025"])
        self.assertEqual(result["coverage"]["parsed_papers"], 7)
        self.assertEqual(result["results"][0]["id"], "fresh")
        self.assertEqual(result["results"][0]["papers"], 4)
        self.assertEqual(result["results"][0]["per_1000"], 571.429)
        train = self.store.usage({"role": "trains_on"})
        self.assertEqual(train["results"][0]["papers"], 1)

    def test_new_includes_zero_adoption_and_uses_body_claims(self):
        result = self.store.new({"topic": "robotics"})
        self.assertIn("unused", [e["id"] for e in result["results"]])
        self.assertEqual(self.store.adoption({"benchmark": "unused"})["evaluates_on"]["others"], 0)

    def test_same_name_same_introducing_paper_is_duplicate_not_homonym(self):
        path = self.root / "config/benchmarks_introduced.json"
        data = json.loads(path.read_text())
        first = data["benchmarks"][0]
        first["registered"] = "fresh"
        duplicate = {**first, "id": "fresh-2", "registered": None, "homonym_of": ["fresh"]}
        data["benchmarks"].append(duplicate)
        self.write("config/benchmarks_introduced.json", data)
        build_snapshot(self.root)
        matches = [e for e in self.store.data()["benchmarks"] if e["name"] == "Fresh"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["merged_ids"], ["fresh-2"])
        self.assertEqual(self.store.adoption({"benchmark": "fresh-2"})["evaluates_on"]["others"], 1)

    def test_duplicate_paper_observations_merge_roles_for_adoption(self):
        path = self.root / "data/processed/benchmark_snapshot.json"
        data = json.loads(path.read_text())
        other = next(p for p in data["papers"] if p["paper_id"] == "2501.00002")
        training = other["uses"]["fresh"]["roles"].pop("trains_on")
        copy = {**other, "edition": "neurips-2025", "uses": {"fresh": {"roles": {"trains_on": training}}}}
        data["papers"].append(copy)
        data["editions"]["neurips-2025"] = {"total_papers": 1, "arxiv_matched": 1, "parsed_papers": 1}
        path.write_text(json.dumps(data))
        result = self.store.adoption({"benchmark": "fresh"})
        self.assertEqual(result["evaluates_on"]["others"], 1)
        self.assertEqual(result["trains_on"]["others"], 1)

    def test_external_adoption_keeps_unknown_and_training_separate(self):
        result = self.store.adoption({"benchmark": "fresh"})
        self.assertEqual(result["evaluates_on"], {"others": 1, "self": 1, "undetermined": 1})
        self.assertEqual(result["trains_on"], {"others": 1, "self": 0, "undetermined": 0})
        self.assertEqual(result["before_claim"], 1)

    def test_new_can_rank_external_adoption_in_one_call(self):
        result = self.store.new({"sort": "adoption", "include_adoption": True})
        self.assertEqual(result["results"][0]["id"], "fresh")
        self.assertEqual(result["results"][0]["adoption"]["evaluates_on"]["others"], 1)
        unused = next(e for e in result["results"] if e["id"] == "unused")
        self.assertEqual(unused["adoption"]["evaluates_on"]["others"], 0)
        self.assertIn("All installed editions", result["adoption_scope"])

    def test_homonyms_need_citation_and_unambiguous_id(self):
        result = self.store.usage({"latest": False})
        ids = [r["id"] for r in result["results"]]
        self.assertNotIn("mirage-old", ids)
        self.assertIn("mirage-new", ids)
        self.assertEqual(result["coverage"]["unattributed_papers"], 1)
        with self.assertRaises(ValueError):
            self.store.trend({"benchmark": "Mirage"})

    def test_trend_includes_observed_zero_and_normalizes(self):
        result = self.store.trend({"benchmark": "toy", "topic": "robotics"})
        self.assertEqual([r["per_1000"] for r in result["results"]], [250, 142.857])
        zero = self.store.trend({"benchmark": "unused"})
        self.assertEqual([r["papers"] for r in zero["results"]], [0, 0])
        self.assertEqual(zero["comparisons"][0]["state"], "no_detected_change")
        with self.assertRaises(ValueError):
            self.store.usage({"topic": "unlabelled specialism"})

    def test_missing_snapshot_is_unavailable_not_zero(self):
        (self.root / "data/processed/benchmark_snapshot.json").unlink()
        self.assertFalse(self.store.scope({})["available"])
        self.assertIn("error", self.store.usage({}))
        self.assertIn("unused", [e["id"] for e in self.store.new({})["results"]])

    def test_answers_verify_quote_and_complete_scoped_figures(self):
        with patch.object(mcp, "B", self.store):
            segs, status = chat.segment(
                "Evidence ⟦arxiv:2501.00002|We evaluate our policies on the fresh benchmark.⟧ "
                "⟦arxiv:2501.00002|We achieved an invented score of 999.⟧", None, None)
        self.assertEqual((status["checked"], status["passed"]), (2, 1))
        self.assertEqual(segs[1]["paper_id"], "2501.00002")
        self.assertTrue(segs[1]["v"])
        run = {"benchmark_usage": self.store.usage}
        vals = figures.recompute("benchmark_usage", '{"topic":"robotics","role":"trains_on"}', {}, run)
        self.assertIn(142.857, vals)
        self.assertNotIn(571.429, vals)
        self.assertNotIn(2501.00002, vals)
        self.assertNotIn(999, vals)
        self.assertEqual(figures.claimed("CIFAR-10: 182 papers, 87.248 per-1000; cvpr-2025 2BY2, 360SPR, 3D-POPE"),
                         [("182", 182.0), ("87.248", 87.248)])
        self.assertEqual(figures.claimed("z −3.374; 10 papers"), [("-3.374", -3.374), ("10", 10.0)])

    def test_assembled_table_cell_is_evidence_but_not_a_verbatim_quote(self):
        path = self.root / "data/processed/benchmark_snapshot.json"
        data = json.loads(path.read_text())
        paper = next(p for p in data["papers"] if p["paper_id"] == "2501.00006")
        evidence = paper["uses"]["toy"]["roles"]["evaluates_on"][0]
        evidence["where"] = "table@appendix:cell"
        path.write_text(json.dumps(data))
        self.assertIsNone(self.store.verify(paper["paper_id"], evidence["text"]))
        self.assertEqual(self.store.evidence({"benchmark": "toy"})["total"], 2)

    def test_export_rejects_source_changed_while_reading(self):
        from bellwether import benchmarks
        original = benchmarks.catalogue
        def mutate(root):
            entries = original(root)
            p = root / "config/benchmarks.json"
            p.write_text(p.read_text() + " ")
            return entries
        with patch.object(benchmarks, "catalogue", mutate):
            with self.assertRaisesRegex(ValueError, "Source changed"):
                build_snapshot(self.root)


class AccountStatus(unittest.TestCase):
    def test_installed_is_not_authenticated_and_no_tokens_returned(self):
        from subprocess import CompletedProcess, TimeoutExpired
        with patch("shutil.which", return_value="/bin/codex"), patch.object(chat.subprocess, "run") as run:
            run.return_value = CompletedProcess([], 1, "", "Not logged in")
            self.assertFalse(chat._agent_status("codex")["authenticated"])
            run.side_effect = TimeoutExpired("codex", 8)
            self.assertIsNone(chat._agent_status("codex")["authenticated"])
            run.side_effect = None
            run.return_value = CompletedProcess([], 0, '{"loggedIn":true,"token":"SECRET"}', "")
            status = chat._agent_status("claude")
            self.assertTrue(status["authenticated"])
            self.assertNotIn("SECRET", json.dumps(status))

    def test_mcp_overrides_use_current_python_without_global_write(self):
        import sys
        config = json.loads(chat._mcp_config())
        self.assertEqual(config["mcpServers"]["bellwether"]["command"], sys.executable)
        self.assertIn("sandbox_mode=\"read-only\"", chat._codex_config_args())
        self.assertIn('mcp_servers.bellwether.default_tools_approval_mode="writes"', chat._codex_config_args())

    def test_mcp_read_only_annotations_are_advertised(self):
        import io
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
        output = io.StringIO()
        with patch.object(mcp.sys, "stdin", io.StringIO(request)), patch.object(mcp.sys, "stdout", output):
            mcp.serve_stdio()
        listed = json.loads(output.getvalue())["result"]["tools"]
        tools = {t["name"]: t for t in listed}
        self.assertTrue(tools["benchmark_usage"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["benchmark_usage"]["annotations"]["destructiveHint"])


if __name__ == "__main__":
    unittest.main()
