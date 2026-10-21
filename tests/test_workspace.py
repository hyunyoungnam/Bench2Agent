"""The local browser workspace: the shared cancel/save rule, the HTTP guards,
jobs that survive a dropped connection, idempotent resends, and conversations
shared with the terminal."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bench2agent import data, providers
from bench2agent.core import mcp
from bench2agent.core.benchmarks import BenchmarkStore
from bench2agent.runtime import ConnectorError
from bench2agent.runtime.base import Run
from bench2agent.session import Session, TurnControl
from bench2agent.workspace.server import make_server

from test_terminal import snapshot


class FakeCLI:
    """A CLI connector that records one tool call and can be held mid-answer."""
    gates: dict = {}
    seen: list = []

    def __init__(self, name="claude", **kw):
        self.name = name

    def ask(self, *, root, revision, question, system, history, provider_session, emit, cancelled, tool_log):
        FakeCLI.seen.append({"question": question, "provider_session": provider_session, "history": len(history or [])})
        call = {"name": "benchmark_usage", "arguments": {"topic": "robotics"}, "result": {"results": [], "revision": revision},
                "revision": revision}
        emit({"t": "tool", "name": call["name"], "arguments": call["arguments"]})
        tool_log.write_text(json.dumps(call) + "\n", encoding="utf-8")
        gate = FakeCLI.gates.get(question)
        if gate is not None:
            deadline = time.time() + 10
            while not gate.is_set() and time.time() < deadline:
                if cancelled():
                    raise ConnectorError("cancelled")
                time.sleep(0.02)
        if cancelled():
            raise ConnectorError("cancelled")
        return Run("Toy is the benchmark in this fixture.", [call], provider_session=f"cli-{len(FakeCLI.seen)}")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        path = self.root / data.MEMBER
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(snapshot()), encoding="utf-8")
        data.install(self.root, file=str(path))
        for p in (patch.object(mcp, "B", BenchmarkStore(self.root)),
                  patch("bench2agent.runtime.connector", side_effect=lambda name, **kw: FakeCLI(name, **kw)),
                  patch("bench2agent.runtime.available_connectors",
                        return_value=[{"name": "claude", "label": "Claude Code", "connected": True},
                                      {"name": "codex", "label": "Codex", "connected": False}])):
            p.start()
            self.addCleanup(p.stop)
        FakeCLI.gates, FakeCLI.seen = {}, []


class TurnControlTests(Base):
    def test_a_cancel_before_the_save_withdraws_and_after_it_is_too_late(self):
        c = TurnControl()
        self.assertEqual(c.cancel(), "cancelled")
        self.assertFalse(c.begin_save())
        c = TurnControl()
        self.assertTrue(c.begin_save())
        self.assertEqual(c.cancel(), "too_late")
        self.assertFalse(c.cancelled())

    def test_a_withdrawn_question_saves_nothing_and_the_cli_session_is_not_resumed(self):
        s = Session(self.root, "anthropic", None, connector="claude")
        s.ask("first", lambda e: None)
        self.assertEqual(s.doc["provider_session"], "cli-1")
        control = TurnControl()
        FakeCLI.gates["second"] = threading.Event()
        threading.Timer(0.2, control.cancel).start()
        with self.assertRaises(providers.Cancelled):
            s.ask("second", lambda e: None, control=control)
        saved = json.loads(s.path.read_text(encoding="utf-8"))
        self.assertEqual([t["q"] for t in saved["turns"]], ["first"])
        self.assertNotIn("provider_session", saved)          # the next question starts from the saved turns
        s.ask("third", lambda e: None)
        self.assertIsNone(FakeCLI.seen[-1]["provider_session"])
        self.assertEqual(FakeCLI.seen[-1]["history"], 1)

    def test_a_cancel_accepted_while_the_answer_finishes_still_saves_nothing(self):
        s = Session(self.root, "anthropic", None, connector="claude")
        control = TurnControl()

        def emit(event):
            control.cancel()                                  # accepted before the save starts
        with self.assertRaises(providers.Cancelled):
            s.ask("finishing", emit, control=control)
        self.assertFalse(s.path.exists())


class ServerTests(Base):
    def setUp(self):
        super().setUp()
        self.httpd, self.ws, self.token = make_server(self.root, port=0, token="t" * 32)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, body=None, *, token=True, host=None, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["X-Workspace-Token"] = self.token
        if origin:
            headers["Origin"] = origin
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def finish(self, job_id):
        since, events = 0, []
        for _ in range(10):                                   # each call waits up to 20 s for news
            st, v = self.call("GET", f"/api/jobs/{job_id}?since={since}")
            events += v["events"]
            since = v["next"]
            if v["state"] != "running":
                return {**v, "events": events}
        self.fail("job did not finish")

    def delete(self, cid, token=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if token:
            headers["X-Workspace-Token"] = self.token
        conn.request("DELETE", f"/api/chats/{cid}", headers=headers)
        r = conn.getresponse()
        body = json.loads(r.read() or b"{}")
        conn.close()
        return r.status, body

    def test_delete_takes_a_conversation_out_of_every_list_and_keeps_its_files(self):
        from bench2agent.session import conversations
        cid = self.call("POST", "/api/chats", {"connector": "claude"})[1]["id"]
        job = self.call("POST", f"/api/chats/{cid}/ask", {"question": "kept", "request_id": "req-00000010"})[1]["job"]
        self.assertEqual(self.finish(job)["state"], "saved")
        chats = self.root / "data/bench2agent/chats"
        logs = sorted(p.name for p in (chats / "toollogs").glob(cid + "-*.jsonl"))
        self.assertTrue(logs)
        stale = Session(self.root, "anthropic", None, cid=cid)           # a terminal that read it before
        self.assertEqual(self.delete(cid, token=False)[0], 403)
        self.assertEqual(self.delete("0" * 12)[0], 404)
        status, r = self.delete(cid)
        self.assertEqual(status, 200)
        kept = self.root / r["kept_in"]
        self.assertTrue((kept / f"{cid}.json").exists())
        self.assertEqual(sorted(p.name for p in (kept / "toollogs").iterdir()), logs)
        self.assertFalse((chats / f"{cid}.json").exists())
        self.assertNotIn(cid, [c["id"] for c in self.call("GET", "/api/chats")[1]["chats"]])
        self.assertNotIn(cid, [c["id"] for c in conversations(self.root)])   # `bench2agent chats` no longer lists it
        self.assertEqual(self.call("GET", f"/api/chats/{cid}")[0], 404)
        with self.assertRaisesRegex(ValueError, "was deleted"):
            stale.ask("after", lambda e: None)                           # never written back
        self.assertFalse((chats / f"{cid}.json").exists())
        self.assertEqual(self.delete(cid)[0], 404)

    def test_delete_waits_for_a_running_question(self):
        cid = self.call("POST", "/api/chats", {"connector": "claude"})[1]["id"]
        FakeCLI.gates["slow"] = threading.Event()
        job = self.call("POST", f"/api/chats/{cid}/ask", {"question": "slow", "request_id": "req-00000011"})[1]["job"]
        self.assertEqual(self.delete(cid)[0], 409)
        self.call("POST", f"/api/jobs/{job}/cancel", {})
        self.assertEqual(self.finish(job)["state"], "cancelled")
        status, r = self.delete(cid)                                     # never saved: nothing to keep
        self.assertEqual((status, r["kept_in"]), (200, None))
        self.assertNotIn(cid, [c["id"] for c in self.call("GET", "/api/chats")[1]["chats"]])

    def test_guards_host_origin_token_and_no_cors(self):
        self.assertEqual(self.call("GET", "/", host="evil.example:80", token=False)[0], 421)
        self.assertEqual(self.call("GET", "/api/state", token=False)[0], 403)
        self.assertEqual(self.call("GET", "/api/state", origin="https://evil.example")[0], 403)
        self.assertEqual(self.call("OPTIONS", "/api/state")[0], 405)
        status, page = self.call("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Bench2Agent</title>", page)
        status, state = self.call("GET", "/api/state", origin=f"http://localhost:{self.port}")
        self.assertEqual(status, 200)
        self.assertEqual([c["name"] for c in state["connectors"] if c["connected"]], ["claude"])
        self.assertEqual(self.call("GET", "/static/../server.py")[0], 404)

    def test_a_question_runs_once_survives_a_dropped_client_and_saves(self):
        cid = self.call("POST", "/api/chats", {"connector": "claude"})[1]["id"]
        FakeCLI.gates["q1"] = gate = threading.Event()
        first = self.call("POST", f"/api/chats/{cid}/ask", {"question": "q1", "request_id": "req-00000001"})[1]
        again = self.call("POST", f"/api/chats/{cid}/ask", {"question": "q1", "request_id": "req-00000001"})[1]
        self.assertEqual(first["job"], again["job"])                     # a resend is the same question
        status, busy = self.call("POST", f"/api/chats/{cid}/ask", {"question": "other", "request_id": "req-00000002"})
        self.assertEqual(status, 409)
        st, chat = self.call("GET", f"/api/chats/{cid}")
        self.assertEqual(chat["active_job"], first["job"])               # a reload reattaches
        gate.set()                                                       # nobody is polling: the job still finishes
        view = self.finish(first["job"])
        self.assertEqual(view["state"], "saved")
        self.assertEqual(view["events"][0]["name"], "benchmark_usage")
        st, chat = self.call("GET", f"/api/chats/{cid}")
        self.assertEqual([t["q"] for t in chat["turns"]], ["q1"])
        st, tools = self.call("GET", f"/api/chats/{cid}/turns/0/tools")
        self.assertEqual(tools["tools"][0]["name"], "benchmark_usage")
        self.assertEqual(len(FakeCLI.seen), 1)                           # the model ran once

    def test_cancel_through_the_api_saves_nothing(self):
        cid = self.call("POST", "/api/chats", {"connector": "claude"})[1]["id"]
        FakeCLI.gates["slow"] = threading.Event()
        job = self.call("POST", f"/api/chats/{cid}/ask", {"question": "slow", "request_id": "req-00000003"})[1]["job"]
        st, r = self.call("POST", f"/api/jobs/{job}/cancel", {})
        self.assertEqual(r["result"], "cancelled")
        self.assertEqual(self.finish(job)["state"], "cancelled")
        self.assertFalse((self.root / "data/bench2agent/chats" / f"{cid}.json").exists())
        st, r = self.call("POST", f"/api/jobs/{job}/cancel", {})
        self.assertEqual(r["result"], "finished")

    def test_the_terminal_and_the_browser_continue_one_conversation_and_never_overwrite(self):
        cid = self.call("POST", "/api/chats", {"connector": "claude"})[1]["id"]
        job = self.call("POST", f"/api/chats/{cid}/ask", {"question": "from the browser", "request_id": "req-00000004"})[1]["job"]
        self.assertEqual(self.finish(job)["state"], "saved")
        terminal = Session(self.root, "anthropic", None, cid=cid)        # `bench2agent --resume ID`
        self.assertEqual(terminal.doc["connector"], "claude")
        stale = Session(self.root, "anthropic", None, cid=cid)
        terminal.ask("from the terminal", lambda e: None)
        job = self.call("POST", f"/api/chats/{cid}/ask", {"question": "browser again", "request_id": "req-00000005"})[1]["job"]
        self.assertEqual(self.finish(job)["state"], "saved")
        st, chat = self.call("GET", f"/api/chats/{cid}")
        self.assertEqual([t["q"] for t in chat["turns"]], ["from the browser", "from the terminal", "browser again"])
        with self.assertRaisesRegex(ValueError, "another terminal"):
            stale.ask("stale", lambda e: None)                           # a copy read earlier is refused


if __name__ == "__main__":
    unittest.main()
