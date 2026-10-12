"""The hosted conversation: real HTTP, real MCP transport, scripted model.

Runs only with the server extra. A uvicorn server is started in a thread on
a free port, so the host's tool calls travel over HTTP MCP to its own mount
exactly as in deployment, and the scripted provider sits where the model API
would be."""
import copy
import json
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
except ImportError:
    from tests.test_revisions import QUOTE_V1, QUOTE_V2, snapshot


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
        r = self.client.post("/api/conversations", json={"provider": "openai", **body}, headers=self.h(user))
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
        self.assertEqual(me["turns_today"], 1)

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
        # the daily limit counts saved and failed turns
        for _ in range(5):
            self.web.log_turn(self.alice["user_id"], conv["cid"], "saved")
        r = self.client.post(f"/api/conversations/{conv['cid']}/ask", json={"question": "one more"}, headers=self.h(self.alice))
        self.assertEqual(r.status_code, 429)
        self.assertEqual(self.client.post(f"/api/conversations/{conv['cid']}/ask", json={"question": "x" * 5000},
                                          headers=self.h(self.alice)).status_code, 422)

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
