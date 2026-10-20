"""The hosted conversation: real HTTP, real MCP transport, scripted model.

Runs only with the server extra. A uvicorn server is started in a thread on
a free port, so the host's tool calls travel over HTTP MCP to its own mount
exactly as in deployment, and the scripted provider sits where the model API
would be."""
import copy
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

try:
    import httpx
    import uvicorn
    from bench2agent.web import app as web_app
except ImportError:  # pragma: no cover
    httpx = None

from bench2agent import data as data_module, providers
from bench2agent.core import revisions
from bench2agent.web.store import WebStore

try:
    from test_revisions import QUOTE_V1, QUOTE_V2, snapshot
    from test_runtime import STUB_CLAUDE
except ImportError:
    from tests.test_revisions import QUOTE_V1, QUOTE_V2, snapshot
    from tests.test_runtime import STUB_CLAUDE


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(httpx, "server extra not installed")
class WebHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "v1.json").write_text(json.dumps(snapshot("v1", QUOTE_V1)))
        (self.root / "v2.json").write_text(json.dumps(snapshot("v2", QUOTE_V2)))
        data_module.install(self.root, file=str(self.root / "v1.json"))
        self.port = free_port()
        self.env = patch.dict(os.environ, {"BENCH2AGENT_WEB_API_CONNECTOR": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.settings = web_app.Settings(self.root, port=self.port, credentials="user", turns_per_day=5)
        self.settings.sweep_seconds = 0.3                                                  # a visible periodic sweep
        self.app = web_app.create_app(self.root, self.settings)
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(0.05)
        self.addCleanup(self.stop)
        self.web = WebStore(self.root / "data/bench2agent/web.sqlite")
        self.alice = self.web.add_user("alice")
        self.bob = self.web.add_user("bob")
        self.base = f"http://127.0.0.1:{self.port}"
        self.client = httpx.Client(base_url=self.base, timeout=60)
        self.addCleanup(self.client.close)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def h(self, user, key="key-of-" + "someone", **extra):
        return {"Authorization": "Bearer " + user["token"], "X-Model-Key": key, **extra}

    @staticmethod
    def scripted(text, tool_args=None):
        return [
            {"status": "completed", "output": [{"type": "function_call", "call_id": "c1", "name": "benchmark_usage",
                                                "arguments": json.dumps(tool_args or {})}]},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
             "usage": {"input_tokens": 10, "output_tokens": 5}},
        ]

    def ask(self, user, cid, question, key="key"):
        with self.client.stream("POST", f"/api/conversations/{cid}/ask", json={"question": question}, headers=self.h(user, key)) as r:
            r.read()
            return r.status_code, [json.loads(line) for line in r.text.splitlines() if line.strip()]

    def new(self, user, **body):
        r = self.client.post("/api/conversations", json={"provider": "openai", "connector": "api", **body}, headers=self.h(user))
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_auth_ownership_and_credential_requirement(self):
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.get("/api/me", headers={"Authorization": "Bearer nope"}).status_code, 401)
        me = self.client.get("/api/me", headers=self.h(self.alice)).json()
        self.assertEqual((me["name"], me["credentials"], me["turns_per_day"]), ("alice", "user", 5))
        conv = self.new(self.alice)
        self.assertEqual(conv["revision"], "v1")
        # bob cannot see, ask in, cancel or delete alice's conversation
        for method, path in (("GET", f"/api/conversations/{conv['cid']}"), ("POST", f"/api/conversations/{conv['cid']}/cancel"),
                             ("DELETE", f"/api/conversations/{conv['cid']}")):
            self.assertEqual(self.client.request(method, path, headers=self.h(self.bob)).status_code, 404, path)
        status, events = self.ask(self.bob, conv["cid"], "Hi")
        self.assertEqual(status, 404)
        self.assertEqual(self.client.get("/api/conversations", headers=self.h(self.bob)).json()["conversations"], [])
        # a question without a model credential is refused before any model call
        r = self.client.post(f"/api/conversations/{conv['cid']}/ask", json={"question": "x"},
                             headers={"Authorization": "Bearer " + self.alice["token"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("X-Model-Key", r.json()["detail"])
        self.web.revoke_user("bob")
        self.assertEqual(self.client.get("/api/me", headers=self.h(self.bob)).status_code, 401)
        self.assertEqual(self.client.get("/", ).status_code, 200)
        self.assertIn("Bench2Agent", self.client.get("/").text)

    def test_turn_runs_tools_over_http_mcp_verifies_and_saves_on_the_conversations_revision(self):
        conv = self.new(self.alice)
        seen = []
        script = self.scripted("Count ⟦benchmark_usage:{}|1 paper⟧ and ⟦arxiv:2501.00001|" + QUOTE_V1 + "⟧ "
                               "but not ⟦arxiv:2501.00001|An invented sentence about results.⟧")

        def post(provider, path, payload):
            seen.append((providers.credential(provider), copy.deepcopy(payload)))
            return script[len(seen) - 1]
        with patch.object(providers, "post", side_effect=post):
            status, events = self.ask(self.alice, conv["cid"], "What is used?", key="alice-secret")
        self.assertEqual(status, 200)
        self.assertEqual([e["t"] for e in events], ["tool", "turn"])
        turn = events[-1]["turn"]
        self.assertEqual((turn["verified"]["checked"], turn["verified"]["passed"], turn["verified"]["fchecked"], turn["verified"]["fpassed"]), (2, 1, 1, 1))
        self.assertEqual(turn["on"]["benchmarks"]["revision"], "v1")
        tool_output = json.loads(seen[1][1]["input"][-1]["output"])         # what the model saw came over HTTP MCP
        self.assertEqual((tool_output["revision"], tool_output["results"][0]["papers"]), ("v1", 1))
        self.assertEqual({k for k, _ in seen}, {"alice-secret"})           # the request's own credential, nothing else
        saved = self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()
        self.assertEqual((len(saved["turns"]), saved["title"]), (1, "What is used?"))
        self.assertNotIn("messages", saved)
        self.assertNotIn("alice-secret", json.dumps(saved))
        self.assertNotIn("alice-secret", (self.root / "data/bench2agent/web.sqlite").read_bytes().decode("latin-1"))
        me = self.client.get("/api/me", headers=self.h(self.alice)).json()
        self.assertEqual((me["attempts_today"], me["answers_today"]), (1, 1))
        self.assertIn("timing", turn)
        self.assertEqual(turn["timing"]["tool_calls"], 1)

    def test_conversation_keeps_v1_after_v2_is_published_and_new_ones_get_v2(self):
        old = self.new(self.alice)
        seen = []

        def post(provider, path, payload):
            seen.append(copy.deepcopy(payload))
            return script[len(seen) - 1]
        script = self.scripted("Count ⟦benchmark_usage:{}|1 paper⟧")
        with patch.object(providers, "post", side_effect=post):
            self.assertEqual(self.ask(self.alice, old["cid"], "First")[1][-1]["t"], "turn")
        data_module.install(self.root, file=str(self.root / "v2.json"))     # v2 goes live while the host runs
        self.assertEqual(self.client.get("/api/revisions", headers=self.h(self.alice)).json()["active"], "v2")
        seen.clear()
        script = self.scripted("Still ⟦benchmark_usage:{}|1 paper⟧ and ⟦arxiv:2501.00002|" + QUOTE_V2 + "⟧")
        with patch.object(providers, "post", side_effect=post):
            status, events = self.ask(self.alice, old["cid"], "Second")
        turn = events[-1]["turn"]
        tool_output = json.loads(seen[1]["input"][-1]["output"])
        self.assertEqual((tool_output["revision"], tool_output["results"][0]["papers"], turn["on"]["benchmarks"]["revision"]), ("v1", 1, "v1"))
        self.assertEqual((turn["verified"]["fpassed"], turn["verified"]["passed"]), (1, 0))   # a v2-only quote does not verify on v1
        new = self.new(self.alice)
        self.assertEqual(new["revision"], "v2")
        seen.clear()
        script = self.scripted("Now ⟦benchmark_usage:{}|2 papers⟧")
        with patch.object(providers, "post", side_effect=post):
            status, events = self.ask(self.alice, new["cid"], "Third")
        self.assertEqual((events[-1]["turn"]["on"]["benchmarks"]["revision"], events[-1]["turn"]["verified"]["fpassed"]), ("v2", 1))
        revisions._rmtree(revisions.revision_dir(self.root, "v1"))          # v1 gone: recovery, not v2's answer
        with patch.object(providers, "post", side_effect=AssertionError("no model call")):
            status, events = self.ask(self.alice, old["cid"], "Fourth")
        self.assertEqual(events[-1]["t"], "error")
        self.assertIn("recovery", events[-1]["message"])
        self.assertEqual(len(self.client.get(f"/api/conversations/{old['cid']}", headers=self.h(self.alice)).json()["turns"]), 2)

    def test_two_users_overlap_without_sharing_history_revision_or_credentials(self):
        a, b = self.new(self.alice), self.new(self.bob)
        gate = threading.Barrier(2, timeout=30)
        seen = {"alice": [], "bob": []}

        def post(provider, path, payload):
            key = providers.credential(provider)
            who = "alice" if key == "alice-key" else "bob"
            seen[who].append(copy.deepcopy(payload))
            if len(seen[who]) == 1:
                gate.wait()                                               # both requests are inside the loop at once
                return self.scripted("")[0]
            return self.scripted(f"Answer for {who} ⟦benchmark_usage:{{}}|1 paper⟧")[1]
        with patch.object(providers, "post", side_effect=post), ThreadPoolExecutor(2) as pool:
            fa = pool.submit(self.ask, self.alice, a["cid"], "alice asks", "alice-key")
            fb = pool.submit(self.ask, self.bob, b["cid"], "bob asks", "bob-key")
            (sa, ea), (sb, eb) = fa.result(timeout=60), fb.result(timeout=60)
        self.assertEqual((sa, sb), (200, 200))
        self.assertIn("alice", json.dumps(ea[-1]["turn"]["segs"]))
        self.assertIn("bob", json.dumps(eb[-1]["turn"]["segs"]))
        self.assertEqual(len(seen["alice"]), 2)
        self.assertEqual(len(seen["bob"]), 2)
        self.assertNotIn("bob asks", json.dumps(seen["alice"]))
        self.assertNotIn("alice asks", json.dumps(seen["bob"]))
        self.assertEqual(self.client.get(f"/api/conversations/{a['cid']}", headers=self.h(self.alice)).json()["turns"][0]["q"], "alice asks")
        self.assertEqual(self.client.get(f"/api/conversations/{b['cid']}", headers=self.h(self.bob)).json()["turns"][0]["q"], "bob asks")

    def test_cancel_saves_nothing_and_quota_is_enforced(self):
        conv = self.new(self.alice)
        started = threading.Event()
        release = threading.Event()

        def slow_post(provider, path, payload):
            if not started.is_set():
                started.set()
                release.wait(timeout=30)
                return self.scripted("")[0]                              # a tool call; the loop then checks the flag
            return self.scripted("late ⟦benchmark_usage:{}|1 paper⟧")[1]
        with patch.object(providers, "post", side_effect=slow_post), ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.ask, self.alice, conv["cid"], "slow question")
            self.assertTrue(started.wait(timeout=30))
            dup = self.client.post(f"/api/conversations/{conv['cid']}/ask", json={"question": "again"}, headers=self.h(self.alice))
            self.assertEqual(dup.status_code, 409)                        # one question at a time per conversation
            cancelled = self.client.post(f"/api/conversations/{conv['cid']}/cancel", headers=self.h(self.alice)).json()
            self.assertTrue(cancelled["cancelled"])
            release.set()
            status, events = future.result(timeout=60)
        self.assertEqual(events[-1]["t"], "error")
        self.assertTrue(events[-1].get("cancelled"))
        self.assertEqual(self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()["turns"], [])
        # the cancelled question counted as an attempt; the daily limit counts attempts
        self.assertEqual(self.web.usage_today(self.alice["user_id"])["by_outcome"], {"cancelled": 1})
        for _ in range(4):
            self.web.reserve_attempt(self.alice["user_id"], conv["cid"], "x", 5)
        r = self.client.post(f"/api/conversations/{conv['cid']}/ask", json={"question": "one more"}, headers=self.h(self.alice))
        self.assertEqual(r.status_code, 429)
        self.assertEqual(self.client.post(f"/api/conversations/{conv['cid']}/ask", json={"question": "x" * 5000},
                                          headers=self.h(self.alice)).status_code, 422)

    def test_service_credentials_mode_uses_the_host_environment(self):
        self.stop()
        self.settings = web_app.Settings(self.root, port=self.port, credentials="service", turns_per_day=5)
        self.app = web_app.create_app(self.root, self.settings)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(0.05)
        seen = []
        script = self.scripted("Count ⟦benchmark_usage:{}|1 paper⟧")

        def post(provider, path, payload):
            seen.append(providers.credential(provider))
            return script[len(seen) - 1]
        with patch.dict("os.environ", {"OPENAI_API_KEY": "service-key"}), patch.object(providers, "post", side_effect=post):
            conv = self.new(self.alice)                                   # the api connector is enabled once the host holds a key
            with self.client.stream("POST", f"/api/conversations/{conv['cid']}/ask", json={"question": "q"},
                                    headers={"Authorization": "Bearer " + self.alice["token"]}) as r:   # no X-Model-Key needed
                r.read()
                events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
        self.assertEqual(events[-1]["t"], "turn")
        self.assertEqual(set(seen), {"service-key"})                      # the environment key reached the provider

    def test_cancel_during_verification_saves_nothing_and_late_cancel_is_refused(self):
        conv = self.new(self.alice)
        verifying = threading.Event()
        release = threading.Event()
        original = web_app.chat.segment

        def slow_segment(*args, **kwargs):
            verifying.set()
            release.wait(timeout=30)
            return original(*args, **kwargs)
        script = self.scripted("Count ⟦benchmark_usage:{}|1 paper⟧")
        with patch.object(providers, "post", side_effect=lambda *a: script.pop(0)), patch.object(web_app.chat, "segment", slow_segment), \
             ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.ask, self.alice, conv["cid"], "slow verification")
            self.assertTrue(verifying.wait(timeout=30))
            cancelled = self.client.post(f"/api/conversations/{conv['cid']}/cancel", headers=self.h(self.alice)).json()
            self.assertTrue(cancelled["cancelled"])                        # accepted while still running
            release.set()
            status, events = future.result(timeout=60)
        self.assertTrue(events[-1].get("cancelled"))
        self.assertEqual(self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()["turns"], [])
        # once saving has started, a cancel is refused and the answer is saved
        committing = threading.Event()
        release2 = threading.Event()
        original_append = self.app.state.web.append_turn

        def slow_append(*args, **kwargs):
            committing.set()
            release2.wait(timeout=30)
            return original_append(*args, **kwargs)
        script = self.scripted("Count ⟦benchmark_usage:{}|1 paper⟧")
        with patch.object(providers, "post", side_effect=lambda *a: script.pop(0)), \
             patch.object(self.app.state.web, "append_turn", slow_append), ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.ask, self.alice, conv["cid"], "late cancel")
            self.assertTrue(committing.wait(timeout=30))
            late = self.client.post(f"/api/conversations/{conv['cid']}/cancel", headers=self.h(self.alice)).json()
            self.assertEqual((late["cancelled"], late["state"]), (False, "committing"))
            self.assertIn("Too late", late["message"])
            release2.set()
            status, events = future.result(timeout=60)
        self.assertEqual(events[-1]["t"], "turn")
        self.assertEqual(len(self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()["turns"]), 1)

    def test_last_attempt_is_granted_to_exactly_one_of_two_concurrent_requests(self):
        a, b = self.new(self.alice), self.new(self.alice)
        for _ in range(4):
            self.web.reserve_attempt(self.alice["user_id"], a["cid"], "x", 5)    # one attempt left today
        gate = threading.Barrier(2, timeout=30)
        hold = threading.Event()

        def post(provider, path, payload):
            hold.wait(timeout=30)
            return self.scripted("done ⟦benchmark_usage:{}|1 paper⟧")[1]

        def go(cid):
            gate.wait()
            return self.client.post(f"/api/conversations/{cid}/ask", json={"question": "race"}, headers=self.h(self.alice))
        with patch.object(providers, "post", side_effect=post), ThreadPoolExecutor(2) as pool:
            fa, fb = pool.submit(go, a["cid"]), pool.submit(go, b["cid"])
            time.sleep(0.5)
            hold.set()
            ra, rb = fa.result(timeout=60), fb.result(timeout=60)
        self.assertEqual(sorted([ra.status_code, rb.status_code]), [200, 429])
        self.assertEqual(self.web.usage_today(self.alice["user_id"])["attempts"], 5)

    def test_dropped_connection_keeps_the_running_slot_until_the_job_ends(self):
        conv = self.new(self.alice)
        started = threading.Event()
        release = threading.Event()

        def slow_post(provider, path, payload):
            started.set()
            release.wait(timeout=30)
            return self.scripted("late ⟦benchmark_usage:{}|1 paper⟧")[1]
        with patch.object(providers, "post", side_effect=slow_post):
            with self.client.stream("POST", f"/api/conversations/{conv['cid']}/ask", json={"question": "slow"}, headers=self.h(self.alice)):
                self.assertTrue(started.wait(timeout=30))              # the client goes away here, mid-question
            dup = self.client.post(f"/api/conversations/{conv['cid']}/ask", json={"question": "again"}, headers=self.h(self.alice))
            self.assertEqual(dup.status_code, 409)                      # the job still holds the slot
            self.assertEqual(self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()["running"], "running")
            release.set()
            for _ in range(100):
                if self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()["running"] is None:
                    break
                time.sleep(0.1)
        saved = self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()
        self.assertEqual((saved["running"], len(saved["turns"])), (None, 1))   # finished and saved on its own

    def test_internal_mcp_mount_needs_the_hosts_token(self):
        for path in ("/mcp-server/revisions", "/mcp-server/health"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 401, path)
            self.assertNotIn("Bearer", r.text)
        self.assertEqual(self.client.post("/mcp-server/revisions/v1/mcp", json={}).status_code, 401)
        self.assertEqual(self.client.get("/mcp-server/revisions", headers=self.h(self.alice)).status_code, 401)   # a user token is not it
        page = self.client.get("/").text
        self.assertNotIn("mcp-server", page)

    def test_conditional_save_refuses_a_stale_version_within_the_same_second(self):
        conv = self.web.create_conversation(self.alice["user_id"], revision="v1", provider="openai", model="m", language="en")
        turn = {"q": "first", "segs": [], "verified": {}, "trail": []}
        self.web.append_turn(self.alice["user_id"], conv["cid"], messages=[{"a": 1}], turn=turn, expected_version=conv["version"])
        with self.assertRaisesRegex(ValueError, "changed while"):
            self.web.append_turn(self.alice["user_id"], conv["cid"], messages=[{"b": 2}], turn={**turn, "q": "stale"}, expected_version=conv["version"])
        saved = self.web.conversation(self.alice["user_id"], conv["cid"])
        self.assertEqual(([t["q"] for t in saved["turns"]], saved["messages"], saved["version"]), (["first"], [{"a": 1}], 1))

    def test_credential_mode_defaults_to_service_when_the_host_has_a_key(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "svc"}, clear=False):
            self.assertEqual(web_app.Settings(self.root).credentials, "service")
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(web_app.Settings(self.root).credentials, "user")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "svc", "BENCH2AGENT_WEB_CREDENTIALS": "user"}, clear=False):
            self.assertEqual(web_app.Settings(self.root).credentials, "user")

    def test_timing_breakdown_is_recorded_per_attempt(self):
        conv = self.new(self.alice)
        script = self.scripted("Count ⟦benchmark_usage:{}|1 paper⟧")
        with patch.object(providers, "post", side_effect=lambda *a: script.pop(0)):
            status, events = self.ask(self.alice, conv["cid"], "timed")
        timing = events[-1]["turn"]["timing"]
        self.assertEqual(sorted(timing), ["attempt", "duration_ms", "model_ms", "tool_calls", "tool_ms", "verify_ms"])
        self.assertGreaterEqual(timing["duration_ms"], timing["model_ms"] + timing["tool_ms"])
        self.assertEqual(timing["tool_calls"], 1)
        row = self.web.attempts("alice")[0]
        self.assertEqual((row["outcome"], row["cid"], row["tool_calls"]), ("saved", conv["cid"], 1))
        self.assertIsNotNone(row["tool_ms"])
        self.assertIsNotNone(row["verify_ms"])
        self.assertNotIn("key", json.dumps(row))

    def test_visitor_session_needs_no_sign_up(self):
        # A fresh machine need not have the official CLI installed.
        # This scenario explicitly provides the test CLI before testing login guidance.
        with self._with_stub_claude():
            browser = httpx.Client(base_url=self.base, timeout=60)
            self.addCleanup(browser.close)
            self.assertEqual(browser.get("/api/me").status_code, 401)
            self.assertEqual(browser.post("/api/session").status_code, 403)                      # the header a form cannot send
            opened = browser.post("/api/session", headers={"X-Requested-With": "Bench2Agent"})
            self.assertEqual((opened.status_code, opened.json()["new"]), (200, True))
            cookie = opened.headers["set-cookie"].lower()
            self.assertIn("httponly", cookie)
            self.assertIn("samesite=lax", cookie)
            me = browser.get("/api/me").json()
            self.assertTrue(me["name"].startswith("visitor-"))
            self.assertFalse(browser.post("/api/session", headers={"X-Requested-With": "Bench2Agent"}).json()["new"])
            self.assertEqual(browser.post("/api/login", json={}).status_code, 404)              # no sign-in, no invites
            listed = browser.get("/api/connectors").json()["connectors"]
            self.assertEqual([c["name"] for c in listed], ["claude", "api"])
            self.assertFalse(listed[0]["connected"])                                                 # nothing connected yet
            r = browser.post("/api/conversations", json={"connector": "claude"}, headers={"X-Requested-With": "Bench2Agent"})
            self.assertEqual(r.status_code, 400)
            self.assertIn("Connect your Claude account", r.json()["detail"])

    def _with_stub_claude(self):
        bindir = self.root / "bin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "claude"
        stub.write_text(STUB_CLAUDE)
        stub.chmod(0o755)
        self.app.state.visitors.passthrough = ("STUB_SKIP_MCP", "STUB_SLEEP", "STUB_FAKE_REVISION", "STUB_RECORD", "STUB_LOGIN_IGNORE_TERM")
        return patch.dict(os.environ, {"PATH": f"{bindir}:{os.environ['PATH']}"})

    def _sign_in(self, user, headers=None):
        """Drive the relayed official sign-in: start, read until it asks, answer."""
        h = headers or self.h(user)
        self.assertFalse(self.client.post("/api/connect/claude/start", headers=h).json()["already"])
        seen, cursor = "", 0
        for _ in range(100):
            r = self.client.get(f"/api/connect/claude/read?since={cursor}", headers=h).json()
            seen += r["output"]; cursor = r["cursor"]
            if r["state"] == "awaiting_code":
                break
            time.sleep(0.05)
        self.assertIn("Paste code here", seen)
        self.assertIn("https://", seen)                                                         # the visitor sees the CLI's own link
        self.client.post("/api/connect/claude/input", json={"text": "stub-code\n"}, headers=h)
        for _ in range(100):
            r = self.client.get(f"/api/connect/claude/read?since={cursor}", headers=h).json()
            cursor = r["cursor"]
            if r["finished"]:
                break
            time.sleep(0.05)
        self.assertEqual(r["state"], "exited_ok")

    def test_visitor_connects_their_own_claude_asks_through_an_isolated_runtime_and_disconnects(self):
        visitors = self.app.state.visitors
        with self._with_stub_claude():
            self._sign_in(self.alice)
            listed = {c["name"]: c for c in self.client.get("/api/connectors", headers=self.h(self.alice)).json()["connectors"]}
            self.assertTrue(listed["claude"]["connected"])
            self.assertEqual(listed["claude"]["account"], "stub@example.org")
            # bob shares the host, not the login
            self.assertFalse({c["name"]: c for c in self.client.get("/api/connectors", headers=self.h(self.bob)).json()["connectors"]}["claude"]["connected"])
            self.assertFalse(visitors.exists(self.bob["user_id"]))
            runtime = visitors.dir(self.alice["user_id"])
            self.assertEqual(oct(runtime.stat().st_mode & 0o777), "0o700")
            self.assertTrue((runtime / "home/.claude/.credentials.json").exists())              # the CLI's own store, in the visitor's runtime
            conv = self.new(self.alice, connector="claude")
            record = self.root / "record.json"
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "operator-secret"}):
                # the stub records its environment through the isolated env only if asked there; ask via the runtime env
                visitors_env = visitors.environment(self.alice["user_id"])
                self.assertNotIn("ANTHROPIC_API_KEY", visitors_env)
                self.assertEqual(visitors_env["HOME"], str(runtime / "home"))
                status, events = self.ask(self.alice, conv["cid"], "robotics?")
            self.assertEqual([e["t"] for e in events], ["tool", "turn"])
            turn = events[-1]["turn"]
            self.assertIsNone(turn["unverifiable"])
            self.assertEqual((turn["connector"], turn["verified"]["fpassed"], turn["verified"]["passed"], turn["on"]["benchmarks"]["revision"]),
                             ("claude", 1, 1, "v1"))
            log = self.root / "data/bench2agent/toollogs" / turn["tool_log"]
            entry = json.loads(log.read_text().splitlines()[0])
            self.assertEqual((entry["name"], entry["revision"], entry["result"]["results"][0]["papers"]), ("benchmark_usage", "v1", 1))
            saved = self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()
            self.assertEqual(saved["provider_session"], "sess-stub-1")
            # a turn whose tool log is missing is saved unverifiable, without marks, and the CLI session is dropped
            with patch.dict(os.environ, {"STUB_SKIP_MCP": "1"}):
                status, events = self.ask(self.alice, conv["cid"], "again?")
            turn = events[-1]["turn"]
            self.assertIn("left no log", turn["unverifiable"])
            self.assertIsNone(turn["verified"])
            self.assertEqual([s["t"] for s in turn["segs"]], ["p"])
            saved = self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()
            self.assertIsNone(saved["provider_session"])
            self.assertEqual(len(saved["turns"]), 2)
            # the next question starts a fresh CLI session carrying the saved turns
            status, events = self.ask(self.alice, conv["cid"], "and now?")
            self.assertEqual(events[-1]["t"], "turn")
            self.assertEqual(self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()["provider_session"], "sess-stub-1")
            # disconnect: official logout and the runtime is gone; conversations stay
            steps = self.client.post("/api/connect/claude/disconnect", headers=self.h(self.alice)).json()
            self.assertEqual((steps["logged_out"], steps["removed"]), (True, True))
            self.assertFalse(runtime.exists())
            self.assertEqual(len(self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()["turns"]), 3)
            self.assertFalse({c["name"]: c for c in self.client.get("/api/connectors", headers=self.h(self.alice)).json()["connectors"]}["claude"]["connected"])
            r = self.client.post(f"/api/conversations/{conv['cid']}/ask", json={"question": "once more"}, headers=self.h(self.alice))
            self.assertEqual(r.status_code, 409)                                                 # re-checked at every question
            self.assertIn("Connect your Claude account", r.json()["detail"])
        events_log = (self.root / "data/bench2agent/visitor-events.jsonl").read_text()
        self.assertNotIn("stub-code", events_log)                                                 # shape only, never content
        self.assertNotIn("https://", events_log)
        self.assertIn('"state": "awaiting_code"', events_log)

    def test_cancel_drops_the_cli_session_and_runtime_limits_hold(self):
        visitors = self.app.state.visitors
        with self._with_stub_claude():
            self._sign_in(self.alice)
            conv = self.new(self.alice, connector="claude")
            self.ask(self.alice, conv["cid"], "first")
            self.assertEqual(self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()["provider_session"], "sess-stub-1")
            started = threading.Event()
            original = visitors.acquire

            def acquire(v, job_id):
                original(v, job_id); started.set()
            with patch.dict(os.environ, {"STUB_SLEEP": "5"}), patch.object(visitors, "acquire", acquire), ThreadPoolExecutor(1) as pool:
                future = pool.submit(self.ask, self.alice, conv["cid"], "slow")
                self.assertTrue(started.wait(timeout=30))
                time.sleep(0.3)
                self.assertTrue(self.client.post(f"/api/conversations/{conv['cid']}/cancel", headers=self.h(self.alice)).json()["cancelled"])
                status, events = future.result(timeout=60)
            self.assertTrue(events[-1].get("cancelled"))
            saved = self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()
            self.assertEqual((saved["provider_session"], len(saved["turns"])), (None, 1))        # dropped; the saved turn stays
            # limits: runtimes
            visitors.max_runtimes = 1
            r = self.client.post("/api/connect/claude/start", headers=self.h(self.bob))
            self.assertEqual(r.status_code, 429)
            visitors.max_runtimes = 50
            # idle sweep removes an old runtime after an official logout
            old = visitors.dir(self.alice["user_id"])
            os.utime(old, (0, 0))
            self.assertEqual(visitors.sweep(), [self.alice["user_id"]])
            self.assertFalse(old.exists())

    def test_slots_are_per_job_and_disconnect_waits_for_running_jobs(self):
        visitors = self.app.state.visitors
        with self._with_stub_claude():
            self._sign_in(self.alice)
            a, b = self.new(self.alice, connector="claude"), self.new(self.alice, connector="claude")
            started = []
            original = visitors.acquire

            def acquire(v, job_id):
                original(v, job_id); started.append(job_id)
            with patch.dict(os.environ, {"STUB_SLEEP": "4"}), patch.object(visitors, "acquire", acquire), ThreadPoolExecutor(3) as pool:
                fa = pool.submit(self.ask, self.alice, a["cid"], "slow a")
                fb = pool.submit(self.ask, self.alice, b["cid"], "slow b")
                for _ in range(100):
                    if len(started) == 2:
                        break
                    time.sleep(0.1)
                self.assertEqual(len(visitors.running_jobs(self.alice["user_id"])), 2)      # two slots for two jobs of one visitor
                # disconnect refuses new work and waits for the jobs; with a short wait it leaves the runtime pending
                steps = self.client.post("/api/connect/claude/disconnect", headers=self.h(self.alice)).json() if False else visitors.disconnect(
                    self.alice["user_id"], stop_running=lambda: True, wait_seconds=0.5)
                self.assertTrue(steps["pending_cleanup"])
                self.assertTrue(visitors.dir(self.alice["user_id"]).exists())                 # nothing deleted under a live process
                r = self.client.post(f"/api/conversations/{a['cid']}/ask", json={"question": "x"}, headers=self.h(self.alice))
                self.assertIn(r.status_code, (409, 400))                                      # refused while closing
                self.assertEqual(self.client.post("/api/connect/claude/start", headers=self.h(self.alice)).status_code, 400)
                fa.result(timeout=60); fb.result(timeout=60)
            self.assertEqual(visitors.running_jobs(self.alice["user_id"]), [])
            for _ in range(50):                                                            # the last job's end completes the deferred cleanup by itself
                if not visitors.dir(self.alice["user_id"]).exists():
                    break
                time.sleep(0.1)
            self.assertFalse(visitors.dir(self.alice["user_id"]).exists())
            self.assertEqual(visitors.finish_pending(), [])                               # nothing left for the sweep
            self.assertFalse(visitors.status(self.alice["user_id"])["pending_cleanup"])
            self.assertFalse({c["name"]: c for c in self.client.get("/api/connectors", headers=self.h(self.alice)).json()["connectors"]}["claude"]["connected"])
            # a disconnect with time to spare completes in one call
            self._sign_in(self.alice)
            steps = self.client.post("/api/connect/claude/disconnect", headers=self.h(self.alice)).json()
            self.assertEqual((steps["pending_cleanup"], steps["removed"], steps["logged_out"]), (False, True, True))

    def test_disconnect_flow_is_atomic_waits_for_sign_in_and_cleans_up_once(self):
        visitors = self.app.state.visitors
        with self._with_stub_claude():
            # (1) no new work can slip in between the state check and the reservation:
            # a reservation attempted from inside the closing sequence is refused
            self._sign_in(self.alice)
            attempts = []

            def stop_running():
                try:
                    visitors.acquire(self.alice["user_id"], "late-job")
                    attempts.append("accepted")
                except Exception as exc:  # noqa: BLE001
                    attempts.append(type(exc).__name__)
                try:
                    visitors.login_start(self.alice["user_id"])
                    attempts.append("login-accepted")
                except Exception as exc:  # noqa: BLE001
                    attempts.append(type(exc).__name__)
                return False
            steps = visitors.disconnect(self.alice["user_id"], stop_running=stop_running)
            self.assertEqual(attempts, ["VisitorError", "VisitorError"])
            self.assertEqual((steps["removed"], steps["pending_cleanup"]), (True, False))
            self.assertEqual(visitors.running_jobs(self.alice["user_id"]), [])
            # (2) a sign-in that ignores SIGTERM: disconnect escalates, confirms the end, then removes
            with patch.dict(os.environ, {"STUB_LOGIN_IGNORE_TERM": "1"}):
                self.assertFalse(self.client.post("/api/connect/claude/start", headers=self.h(self.alice)).json()["already"])
                for _ in range(100):
                    if self.client.get("/api/connect/claude/read?since=0", headers=self.h(self.alice)).json()["state"] == "awaiting_code":
                        break
                    time.sleep(0.05)
                t = time.perf_counter()
                steps = visitors.disconnect(self.alice["user_id"], wait_seconds=1.0)
            self.assertEqual((steps["removed"], steps["pending_cleanup"]), (True, False))
            self.assertLess(time.perf_counter() - t, 10)
            self.assertFalse(visitors.dir(self.alice["user_id"]).exists())
            events = (self.root / "data/bench2agent/visitor-events.jsonl").read_text()
            self.assertIn('"state": "cancelled"', events)
            # (3) removal fails once: the visitor stays pending, nothing is half-deleted, the retry succeeds and clears it
            self._sign_in(self.alice)
            runtime = visitors.dir(self.alice["user_id"])
            original_rmtree = visitors.__class__.__module__ and __import__("shutil").rmtree
            calls = []

            def failing_rmtree(path, *a, **k):
                calls.append(path)
                if len(calls) == 1:
                    raise OSError("busy")
                return original_rmtree(path, *a, **k)
            with patch("bench2agent.web.visitors.shutil.rmtree", side_effect=failing_rmtree):
                steps = visitors.disconnect(self.alice["user_id"])
                self.assertEqual((steps["removed"], steps["pending_cleanup"]), (False, True))
                self.assertTrue(runtime.exists())
                self.assertTrue(visitors.status(self.alice["user_id"])["pending_cleanup"])
                r = self.client.post("/api/connect/claude/start", headers=self.h(self.alice))
                self.assertEqual(r.status_code, 400)                                  # still closing: no new work
                # the retry: this call, or the end of the sign-in process just before it (_on_process_end),
                # whichever claims it first; removal still runs exactly once (two rmtree calls below)
                self.assertIn(visitors.finish_pending(), ([self.alice["user_id"]], []))
            self.assertFalse(runtime.exists())
            self.assertFalse(visitors.status(self.alice["user_id"])["pending_cleanup"])
            self.assertEqual(len(calls), 2)
            # (4) the last job's end and the periodic sweep overlap: removal runs exactly once
            self._sign_in(self.alice)
            conv = self.new(self.alice, connector="claude")
            started = threading.Event()
            original_acquire = visitors.acquire

            def acquire(v, job_id):
                original_acquire(v, job_id); started.set()
            removals = []
            original_remove = visitors._remove

            def counting_remove(v):
                removals.append(v); time.sleep(0.3)
                return original_remove(v)
            with patch.dict(os.environ, {"STUB_SLEEP": "3"}), patch.object(visitors, "acquire", acquire), \
                 patch.object(visitors, "_remove", counting_remove), ThreadPoolExecutor(2) as pool:
                future = pool.submit(self.ask, self.alice, conv["cid"], "slow")
                self.assertTrue(started.wait(timeout=30))
                steps = visitors.disconnect(self.alice["user_id"], stop_running=lambda: True, wait_seconds=0.2)
                self.assertTrue(steps["pending_cleanup"])
                sweeps = pool.submit(lambda: [visitors.sweep() for _ in range(20)])
                future.result(timeout=60)                                              # the job ends: release() cleans up
                sweeps.result(timeout=60)
            for _ in range(50):
                if not visitors.dir(self.alice["user_id"]).exists():
                    break
                time.sleep(0.1)
            self.assertFalse(visitors.dir(self.alice["user_id"]).exists())
            self.assertEqual(removals, [self.alice["user_id"]])                        # once, whichever thread won
            self.assertFalse(visitors.status(self.alice["user_id"])["pending_cleanup"])

    def test_periodic_sweep_runs_with_the_server(self):
        visitors = self.app.state.visitors
        with self._with_stub_claude():
            self._sign_in(self.alice)
            runtime = visitors.dir(self.alice["user_id"])
            with patch("bench2agent.web.visitors.shutil.rmtree", side_effect=OSError("busy")):
                self.assertTrue(visitors.disconnect(self.alice["user_id"])["pending_cleanup"])
            self.assertTrue(runtime.exists())
            # the server's sweeper (short interval in this test's settings) completes it without any call from here
            for _ in range(100):
                if not runtime.exists():
                    break
                time.sleep(0.1)
            self.assertFalse(runtime.exists())
            self.assertTrue(visitors._sweeper.is_alive())
        self.stop()
        self.assertFalse(visitors._sweeper.is_alive())                                    # stopped with the server

    def test_unverifiable_then_next_then_cancel_then_resume(self):
        visitors = self.app.state.visitors
        with self._with_stub_claude():
            self._sign_in(self.alice)
            conv = self.new(self.alice, connector="claude")
            self.ask(self.alice, conv["cid"], "verified one")
            with patch.dict(os.environ, {"STUB_SKIP_MCP": "1"}):
                status, events = self.ask(self.alice, conv["cid"], "unverifiable one")
            self.assertIn("left no log", events[-1]["turn"]["unverifiable"])
            record = self.root / "record.json"
            with patch.dict(os.environ, {"STUB_RECORD": str(record)}):
                status, events = self.ask(self.alice, conv["cid"], "next one")
            argv = json.loads(record.read_text())["argv"]
            prompt = argv[argv.index("-p") + 1]
            self.assertNotIn("--resume", argv)                                 # fresh session after the unverifiable turn
            self.assertIn("verified one", prompt)
            self.assertNotIn("unverifiable one", prompt)                       # kept for the reader, never context
            # cancel, then resume: the cancelled question is not context either
            started = threading.Event()
            original = visitors.acquire

            def acquire(v, job_id):
                original(v, job_id); started.set()
            with patch.dict(os.environ, {"STUB_SLEEP": "5"}), patch.object(visitors, "acquire", acquire), ThreadPoolExecutor(1) as pool:
                future = pool.submit(self.ask, self.alice, conv["cid"], "cancelled one")
                self.assertTrue(started.wait(timeout=30)); time.sleep(0.3)
                self.assertTrue(self.client.post(f"/api/conversations/{conv['cid']}/cancel", headers=self.h(self.alice)).json()["cancelled"])
                future.result(timeout=60)
            with patch.dict(os.environ, {"STUB_RECORD": str(record)}):
                status, events = self.ask(self.alice, conv["cid"], "resumed one")
            argv = json.loads(record.read_text())["argv"]
            prompt = argv[argv.index("-p") + 1]
            self.assertNotIn("--resume", argv)
            self.assertNotIn("cancelled one", prompt)
            self.assertIn("next one", prompt)
            saved = self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()
            self.assertEqual([t["q"] for t in saved["turns"]], ["verified one", "unverifiable one", "next one", "resumed one"])
            self.assertEqual(saved["provider_session"], "sess-stub-1")

    def test_provider_failure_saves_nothing_and_echoes_no_secret(self):
        conv = self.new(self.alice)
        with patch.object(providers, "post", side_effect=providers.ProviderError("openai: API key rejected (HTTP 401).")):
            status, events = self.ask(self.alice, conv["cid"], "Hello", key="super-secret")
        self.assertEqual(events[-1]["t"], "error")
        self.assertIn("API key rejected", events[-1]["message"])
        self.assertNotIn("super-secret", json.dumps(events))
        self.assertEqual(self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()["turns"], [])


if __name__ == "__main__":
    unittest.main()
