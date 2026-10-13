"""The interactive terminal under a real pseudo-terminal with a stub Claude
Code: /new keeps the connector, /resume asks for no API key on a CLI
conversation, a real Ctrl+C cancels and ends the child CLI, and the next
question after a cancel or a failure goes through. Nothing here calls a
model or needs a key."""
import json
import os
import pty
import re
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from bench2agent import data as data_module

try:
    from test_revisions import QUOTE_V1, snapshot
    from test_runtime import STUB_CLAUDE
except ImportError:
    from tests.test_revisions import QUOTE_V1, snapshot
    from tests.test_runtime import STUB_CLAUDE

SRC = str(Path(__file__).resolve().parents[1] / "src")


class Terminal:
    """A child `bench2agent` on a PTY: write lines, read until a pattern."""

    def __init__(self, root: Path, env: dict, *args: str):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(root)
            os.execvpe(sys.executable, [sys.executable, "-m", "bench2agent", "--home", str(root), *args], env)
        self.buf = ""

    def expect(self, pattern: str, timeout: float = 60.0) -> str:
        end = time.time() + timeout
        while time.time() < end:
            if re.search(pattern, self.buf, re.S):
                out, self.buf = self.buf, ""
                return out
            r, _, _ = select.select([self.fd], [], [], 0.5)
            if r:
                try:
                    chunk = os.read(self.fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                self.buf += chunk.decode("utf-8", "replace")
        raise AssertionError(f"pattern {pattern!r} not seen; buffer: {self.buf[-1500:]!r}")

    def send(self, text: str) -> None:
        os.write(self.fd, text.encode())

    def interrupt(self) -> None:
        os.write(self.fd, b"\x03")           # a real Ctrl+C through the terminal

    def close(self) -> None:
        try:
            self.send("/exit\n")
            os.waitpid(self.pid, 0)
        except (OSError, ChildProcessError):
            pass


class TerminalPtyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "v1.json").write_text(json.dumps(snapshot("v1", QUOTE_V1)))
        data_module.install(self.root, file=str(self.root / "v1.json"))
        bindir = self.root / "bin"
        bindir.mkdir()
        stub = bindir / "claude"
        stub.write_text(STUB_CLAUDE)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "STUB_SLEEP", "STUB_SKIP_MCP")}
        self.env.update({"PATH": f"{bindir}:{os.environ['PATH']}", "PYTHONPATH": SRC, "TERM": "dumb", "HOME": str(self.root)})
        self.stub_marker = str(bindir / "claude")

    def stub_processes(self) -> list[str]:
        out = subprocess.run(["pgrep", "-f", self.stub_marker], capture_output=True, text=True)
        return [p for p in out.stdout.split() if p.strip()]

    def chats(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in (self.root / "data/bench2agent/chats").glob("*.json")]

    def test_new_resume_ctrl_c_and_follow_up_on_a_real_terminal(self):
        t = Terminal(self.root, self.env, "--connector", "claude", "--model", "m")
        self.addCleanup(t.close)
        t.expect(r"> $")
        t.send("first question\n")
        out = t.expect(r"Quotes 1/1 · Figures 1/1.*> $")
        self.assertIn("Toy", out)
        first = t.expect if False else None
        cid1 = next(c["id"] for c in self.chats())
        self.assertEqual(next(c for c in self.chats())["connector"], "claude")
        self.assertEqual(next(c for c in self.chats())["provider_session"], "sess-stub-1")
        # /new keeps the connector; no API key is asked for
        t.send("/new\n")
        out = t.expect(r"New conversation · [0-9a-f]{12} · claude.*> $")
        t.send("second conversation question\n")
        t.expect(r"Quotes 1/1.*> $")
        self.assertEqual(sorted(c["connector"] for c in self.chats()), ["claude", "claude"])
        # /resume the first conversation: a CLI conversation needs no key prompt
        t.send(f"/resume {cid1}\n")
        out = t.expect(r"Resumed · " + cid1 + r".*> $")
        self.assertNotIn("API_KEY", out)
        # a real Ctrl+C in the middle of a slow answer: the turn is dropped, the
        # child CLI is gone, and the CLI's own session id is forgotten
        t.send("/exit\n")
        t.expect(r"Continue:.*")
        os.waitpid(t.pid, 0)
        env_slow = {**self.env, "STUB_SLEEP": "20"}
        t2 = Terminal(self.root, env_slow, "--connector", "claude", "--model", "m", "--resume", cid1)
        self.addCleanup(t2.close)
        t2.expect(r"> $")
        t2.send("slow question\n")
        for _ in range(100):
            if self.stub_processes():
                break
            time.sleep(0.1)
        self.assertTrue(self.stub_processes(), "the stub CLI should be running")
        t2.interrupt()
        out = t2.expect(r"Cancelled; the unfinished turn was not saved\..*> $")
        for _ in range(50):
            if not self.stub_processes():
                break
            time.sleep(0.2)
        self.assertEqual(self.stub_processes(), [], "the child CLI must end on cancel")
        doc = next(c for c in self.chats() if c["id"] == cid1)
        self.assertIsNone(doc.get("provider_session"))
        self.assertEqual(len(doc["turns"]), 1)
        # the follow-up in the same terminal works: no "changed in another terminal"
        t2.send("follow-up after cancel\n")
        out = t2.expect(r"Quotes 1/1 · Figures 1/1.*> $")
        self.assertNotIn("another terminal", out)
        doc = next(c for c in self.chats() if c["id"] == cid1)
        self.assertEqual((len(doc["turns"]), doc.get("provider_session")), (2, "sess-stub-1"))
        # a failure (the stub never reaches the MCP server) saves an unverifiable turn, drops the session, and the next question still works
        t2.send("/exit\n")
        t2.expect(r"Continue:.*")
        os.waitpid(t2.pid, 0)
        env_fail = {**self.env, "STUB_SKIP_MCP": "1"}
        t3 = Terminal(self.root, env_fail, "--connector", "claude", "--model", "m", "--resume", cid1)
        self.addCleanup(t3.close)
        t3.expect(r"> $")
        t3.send("unverifiable question\n")
        out = t3.expect(r"> $", timeout=90)
        doc = next(c for c in self.chats() if c["id"] == cid1)
        self.assertEqual(len(doc["turns"]), 3)
        self.assertIn("left no log", doc["turns"][-1]["unverifiable"] or "")
        self.assertIsNone(doc.get("provider_session"))
        t3.send("/exit\n")
        os.waitpid(t3.pid, 0)
        # the next terminal, same conversation: the unverifiable turn is kept but never becomes context
        record = self.root / "record.json"
        t4 = Terminal(self.root, {**self.env, "STUB_RECORD": str(record)}, "--connector", "claude", "--model", "m", "--resume", cid1)
        self.addCleanup(t4.close)
        t4.expect(r"> $")
        t4.send("after the unverifiable one\n")
        t4.expect(r"Quotes 1/1.*> $")
        argv = json.loads(record.read_text())["argv"]
        prompt = argv[argv.index("-p") + 1]
        self.assertNotIn("--resume", argv)                               # fresh session after the unverifiable turn
        self.assertIn("first question", prompt)                          # verified turns are the context
        self.assertIn("follow-up after cancel", prompt)
        self.assertNotIn("unverifiable question", prompt)                # the unverifiable one is not

    def test_second_terminal_cannot_overwrite_a_newer_turn(self):
        from bench2agent.session import Session
        a = Session(self.root, "openai", "m", connector="claude")
        b_env = self.env
        with unittest.mock.patch.dict(os.environ, {"PATH": b_env["PATH"]}):
            a.ask("one", lambda e: None)
            b = Session(self.root, "openai", "m", cid=a.doc["id"])
            a.ask("two", lambda e: None)                                 # a moves the file on
            with self.assertRaisesRegex(ValueError, "another terminal"):
                b.ask("stale", lambda e: None)
            b._drop_provider_session()                                   # a stale session never writes over a's turns
            saved = json.loads(a.path.read_text())
            self.assertEqual([t["q"] for t in saved["turns"]], ["one", "two"])
            self.assertEqual(saved["provider_session"], "sess-stub-1")

    def test_fresh_interactive_cli_selects_signed_in_agent_without_key_or_model_prompts(self):
        config = self.root / ".claude"
        config.mkdir()
        (config / ".credentials.json").write_text("{}")
        # Prevent the real machine's Codex login from being selected.
        codex = self.root / "bin/codex"
        codex.write_text("#!/usr/bin/env python3\nimport sys\nprint('Not logged in'); sys.exit(1)\n")
        codex.chmod(codex.stat().st_mode | stat.S_IEXEC)
        record = self.root / "record.json"
        t = Terminal(self.root, {**self.env, "STUB_RECORD": str(record)})
        self.addCleanup(t.close)
        out = t.expect(r"Bench2Agent · Claude Code / CLI default.*> $")
        self.assertNotIn("API_KEY", out)
        self.assertNotIn("Model provider", out)
        t.send("first question without setup\n")
        t.expect(r"Quotes 1/1 · Figures 1/1.*> $")
        self.assertEqual(self.chats()[0]["connector"], "claude")
        self.assertEqual(json.loads((self.root / "data/bench2agent/settings.json").read_text())["connector"], "claude")
        self.assertNotIn("--model", json.loads(record.read_text())["argv"])


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
