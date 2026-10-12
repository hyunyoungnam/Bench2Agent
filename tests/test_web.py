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
        self.settings = web_app.Settings(self.root, port=self.port, credentials="user", turns_per_day=5)
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

    def test_invite_sign_in_opens_a_session_cookie_with_csrf_protection(self):
        code = self.web.add_invite("tester", uses=1)["code"]
        browser = httpx.Client(base_url=self.base, timeout=60)
        self.addCleanup(browser.close)
        # sign-in needs the custom header a cross-site form cannot send
        self.assertEqual(browser.post("/api/login", json={"invite": code, "name": "carol"}).status_code, 403)
        r = browser.post("/api/login", json={"invite": code, "name": "carol"}, headers={"X-Requested-With": "Bench2Agent"})
        self.assertEqual((r.status_code, r.json()["name"]), (200, "carol"))
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertNotIn("sess_", r.text)                                  # the session id lives in the cookie only
        me = browser.get("/api/me").json()                                 # the cookie now authenticates
        self.assertEqual((me["name"], me["auth"]), ("carol", "session"))
        # the invite is used up and the name is taken
        self.assertEqual(browser.post("/api/login", json={"invite": code, "name": "dave"}, headers={"X-Requested-With": "Bench2Agent"}).status_code, 400)
        # state changes from the browser session need the header; reads do not
        self.assertEqual(browser.post("/api/conversations", json={"provider": "openai"}).status_code, 403)
        r = browser.post("/api/conversations", json={"provider": "openai"}, headers={"X-Requested-With": "Bench2Agent"})
        self.assertEqual(r.status_code, 201)
        cid = r.json()["cid"]
        self.assertEqual(browser.get(f"/api/conversations/{cid}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/conversations/{cid}", headers=self.h(self.alice)).status_code, 404)   # not alice's
        # sign out closes the session
        self.assertEqual(browser.post("/api/logout", headers={"X-Requested-With": "Bench2Agent"}).status_code, 200)
        self.assertEqual(browser.get("/api/me").status_code, 401)
        self.assertEqual(self.client.get("/api/me", headers={"Cookie": "b2a_session=sess_forged"}).status_code, 401)
        self.assertEqual([i["uses_left"] for i in self.web.invites()], [0])

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

    def test_open_access_session_needs_no_sign_up_and_hosted_mode_needs_an_invite(self):
        self.assertTrue(self.settings.local_mode)
        browser = httpx.Client(base_url=self.base, timeout=60)
        self.addCleanup(browser.close)
        self.assertEqual(browser.get("/api/me").status_code, 401)
        self.assertEqual(browser.post("/api/session").status_code, 403)                     # header required
        opened = browser.post("/api/session", headers={"X-Requested-With": "Bench2Agent"})
        self.assertEqual((opened.status_code, opened.json()["new"]), (200, True))
        me = browser.get("/api/me").json()
        self.assertTrue(me["name"].startswith("visitor-"))
        self.assertEqual((me["access"], me["local_mode"]), ("open", True))
        again = browser.post("/api/session", headers={"X-Requested-With": "Bench2Agent"}).json()
        self.assertFalse(again["new"])                                                     # the same visitor keeps the session
        r = browser.post("/api/conversations", json={"connector": "api", "provider": "openai"}, headers={"X-Requested-With": "Bench2Agent"})
        self.assertEqual(r.status_code, 201)
        # a hosted (non-loopback) configuration asks for an invite and offers no CLI connectors
        hosted = web_app.Settings(self.root, host="0.0.0.0", port=self.port + 1, credentials="user")
        self.assertEqual((hosted.access, hosted.local_mode), ("invite", False))

    def test_connectors_are_listed_with_enablement_and_a_cli_connector_answers_through_the_pinned_mcp(self):
        bindir = self.root / "bin"
        bindir.mkdir()
        stub = bindir / "claude"
        stub.write_text(STUB_CLAUDE)
        stub.chmod(0o755)
        import shutil
        real_which = shutil.which
        with patch.dict(os.environ, {"PATH": f"{bindir}:{os.environ['PATH']}"}), \
             patch("bench2agent.runtime.base.shutil.which", side_effect=lambda b, *a, **k: None if b == "codex" else real_which(b, *a, **k)):
            listed = {c["name"]: c for c in self.client.get("/api/connectors", headers=self.h(self.alice)).json()["connectors"]}
            self.assertFalse(listed["codex"]["enabled"])                                        # codex absent on this (patched) host
            self.assertTrue(listed["claude"]["connected"] and listed["claude"]["enabled"])       # loopback: the host's login may answer
            self.assertEqual(listed["claude"]["account"], "stub@example.org")
            self.assertIn("public_hosting", listed["claude"])
            self.assertEqual(listed["claude"]["login"]["kind"], "command")
            self.assertTrue(listed["api"]["enabled"])                                           # user mode: a key per request
            conv = self.new(self.alice, connector="claude")
            self.assertEqual(conv["connector"], "claude")
            # no model key needed: the official CLI's own login answers; Bench2Agent records the loop
            with self.client.stream("POST", f"/api/conversations/{conv['cid']}/ask", json={"question": "robotics?"},
                                    headers={"Authorization": "Bearer " + self.alice["token"]}) as r:
                r.read()
                events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
            self.assertEqual([e["t"] for e in events], ["tool", "turn"])
            turn = events[-1]["turn"]
            self.assertEqual((turn["connector"], turn["trail"], turn["on"]["benchmarks"]["revision"]),
                             ("claude", [{"name": "benchmark_usage", "arg": '{"topic": "robotics"}'}], "v1"))
            self.assertEqual((turn["verified"]["fpassed"], turn["verified"]["passed"]), (1, 1))    # the stub's figure and quote verify on v1
            log = self.root / "data/bench2agent/toollogs" / turn["tool_log"]
            record = json.loads(log.read_text().splitlines()[0])
            self.assertEqual((record["name"], record["revision"], record["result"]["results"][0]["papers"]), ("benchmark_usage", "v1", 1))
            saved = self.client.get(f"/api/conversations/{conv['cid']}", headers=self.h(self.alice)).json()
            self.assertEqual(saved["provider_session"], "sess-stub-1")                          # the CLI's own conversation id, for the next turn
            # a connector that is not installed cannot be chosen
            r = self.client.post("/api/conversations", json={"connector": "codex"}, headers=self.h(self.alice))
            self.assertEqual(r.status_code, 400)
            self.assertIn("not available here", r.json()["detail"])

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
