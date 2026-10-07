"""The conversational loop: browser chat -> the user's own coding agent.

This is the OpenResearch-shaped frame with our difference inside it: the
installed agent CLI runs headless using credentials managed by that CLI,
with the bellwether MCP tools, and every
factual sentence it writes must carry an anchor `⟦gid|exact quote⟧`. The
server verifies each anchor against the locally held corpus BEFORE the
browser shows it, so the reader sees, per citation, whether the quote really
exists in the paper. Prose can be wrong; a green check cannot.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import threading
from collections import deque
from pathlib import Path

from . import figures
from .mcp import Store, _VENUE
from .verify import Verifier

from .paths import ROOT, agent_environment
CHAT_DIR = ROOT / "data" / "chats"

SYSTEM = (
    "You help researchers choose evaluation benchmarks using Bellwether's local paper evidence. "
    "BENCHMARK QUESTIONS: first call benchmark_scope to discover installed editions, coverage and field labels. "
    "For what people evaluate on now, use benchmark_usage with evaluates_on, latest=true and an exact field label. "
    "For trends use benchmark_trend, for introductions use new_benchmarks, and for uptake use benchmark_adoption. "
    "Do not substitute benchmark_info's abstract mentions for evaluation use. If the full-text snapshot is absent, "
    "say usage is unavailable; never invent counts or treat missing data as zero. "
    "Always name the analysed editions, the parsed-paper denominator and source coverage. Latest analysed "
    "editions differ by venue and are not necessarily the current calendar year. Keep evaluation and training, "
    "benchmark and training-resource introductions, and other/self/undetermined authors separate. "
    "First claim means first reviewed introduction in this corpus, not the world's first release. "
    "If before_claim is positive, explain the observed use before the claim; do not describe that item "
    "as newly created at the claim edition. For most-adopted introductions, use new_benchmarks(sort=adoption) "
    "rather than extrapolating from its default first page. "
    "Report unresolved same-name uses alongside attributed counts; zero attributed uses with unresolved "
    "observations is not zero adoption. Field filtering excludes unlabelled papers, including entire "
    "venues if their labels are absent; state that limit rather than claiming full domain coverage. "
    "List new benchmarks even without uptake. Usage frequency is not quality. Unknown field labels require "
    "disambiguation; do not silently broaden the question. Get benchmark_evidence for underlying experiments. "
    "Cite verbatim role/claim evidence as ⟦arxiv:2301.00001|exact quote⟧, copying the returned paper_id. "
    "Table-cell evidence ending in :cell is an assembled record, not a verbatim sentence to quote. "
    "For numbers from benchmark tools, use ⟦benchmark_usage:{\"topic\":\"robotics\"}|figures⟧ with the complete "
    "JSON arguments of that call (including role, venue, years and latest when supplied). "
    "Only call evidence tools; do not modify files, execute shell commands or browse independently. "
    "Evidence comes ONLY from the bellwether MCP tools; never answer "
    "about papers from memory. METHOD for field-level questions (what is "
    "rising, what is new this year, where are the gaps): read the field in "
    "bulk with field_cards, get computed shares from field_trend, then derive "
    "your conclusion FROM the cards — recurring key_change ideas are a "
    "technique rising, recurring limitations nobody's key_change answers are "
    "a gap. Distribution figures must be restated exactly as the tools "
    "computed them (per-1k shares, z), never invented. For GAP / blue-ocean "
    "questions call gap_scan(topic): the reader sees its derivation tree "
    "rendered directly, so do NOT restate every branch — interpret it: which "
    "gap candidates look real, which are lexical artifacts, anchored to "
    "specific papers. For DEPTH questions "
    "about one paper (how it works, its numbers), read its actual text with "
    "paper_text: list sections, then read the relevant one. PROTOCOL: after each "
    "claim about a specific paper, append an anchor of the exact form "
    "⟦gid|quote⟧ where quote is copied verbatim from a tool response (a card "
    "field or abstract sentence, >=20 chars, never edited). Claims without "
    "an anchor will be shown to the reader as unbacked. FIGURES: every number "
    "you print is checked against what the tools returned this turn — restate "
    "them exactly as computed and never round a share differently. When a "
    "figure matters enough to bind to one call, anchor it as "
    "⟦tool:argument|the figures⟧, e.g. ⟦gap_scan:healthcare|31 name it, 3 "
    "attack it⟧, using exactly the argument you called. If the corpus cannot "
    "answer, say so plainly. For benchmark questions, request location links only when asked; "
    "benchmark_info is a location lookup, never a fallback for usage or evidence. "
    "LINKS FOR PAPER QUESTIONS: when the answer centres on one "
    "paper or a handful, call paper_resources for each and print what it "
    "returns — the paper's own code / data / model / page URLs, and where "
    "each benchmark it names lives. A reader who now has the paper wants the "
    "artifact next and should not have to ask a second question for it. Print "
    "nothing when it returns none: no link is not evidence of no code, only "
    "that the paper's text on file prints none. Skip this for field-level "
    "answers that cite many papers. Their links are either printed in the paper or "
    "our API-checked mapping, and stars/downloads are facts to report, never "
    "a reason to rank. ALWAYS write your answer in English, whatever "
    "language the question is in — the reader's Korean is rendered from this "
    "English by a separate step, so one text is written, verified and stored. "
    "Never rank papers by importance. Keep answers "
    "compact — a few sentences with anchors beat an essay."
)

# The interface language no longer changes what the agent writes — it always
# writes English, and Korean is rendered from it. Kept as a seam because the
# callers still pass a language and a second renderer may want it.
def system_for(lang: str | None) -> str:
    return SYSTEM

# Two kinds of anchor, one scan so the segments come out in reading order:
#   ⟦gid|quote⟧              a sentence, matched against that paper
#   ⟦tool:arg|figures⟧       a number, recomputed by running the tool again
_ANCHOR = re.compile(
    r"⟦\s*(?:(\d+)\s*\|([^⟧]+)|([a-z_]{3,20})\s*:\s*([^|⟧]{1,2000})\|([^⟧]{1,2000}))⟧")


def _mcp_config() -> str:
    return json.dumps({"mcpServers": {"bellwether": {
        "command": sys.executable, "args": ["-m", "bellwether", "mcp"],
        "env": agent_environment()}}})


def ask(message: str, sid: str | None = None, timeout: int = 300) -> dict:
    cmd = ["claude", "-p", message, "--output-format", "json",
           "--max-turns", "12",
           "--mcp-config", _mcp_config(), "--strict-mcp-config",
           "--allowedTools", "mcp__bellwether",
           "--append-system-prompt", SYSTEM]
    if sid:
        cmd += ["--resume", sid]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                         timeout=timeout, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        return {"error": (out.stderr or out.stdout or "agent failed")[-400:]}
    d = json.loads(out.stdout)
    return {"text": d.get("result") or "", "sid": d.get("session_id"),
            "turns": d.get("num_turns")}


def segment(text: str, store: Store, ver: Verifier,
            trail: list | None = None) -> tuple[list, dict]:
    """Prose, checked quotes, recomputed figures — and every OTHER number too.

    The anchors are the agent's declaration; the automatic pass is ours. Both
    end in the same place: a number is either in what the tools returned this
    turn, derivable from two of those, or in neither."""
    segs: list[dict] = []
    v = {"checked": 0, "passed": 0, "fchecked": 0, "fpassed": 0}
    fcache: dict = {}
    pos = 0
    for m in _ANCHOR.finditer(text):
        if m.start() > pos:
            segs.append({"t": "p", "s": text[pos:m.start()]})
        pos = m.end()
        if m.group(1):                                   # ⟦gid|quote⟧
            gid, quote = int(m.group(1)), m.group(2).strip()
            role = ver.role(gid, quote)
            ok = role is not None
            v["checked"] += 1
            v["passed"] += ok
            try:
                r = store.rec(gid)
                key = store.where(gid)[0] if r else None
            except (FileNotFoundError, ValueError, KeyError):
                # Portable benchmark installs do not need the older gid index.
                # An invented/legacy anchor remains visibly unverified.
                r, key = None, None
            venue, year = (key.rsplit("-", 1) if key else (None, None))
            segs.append({"t": "c", "gid": gid, "q": quote, "v": ok, "role": role,
                         "title": r["title"] if r else f"gid {gid}",
                         "venue": _VENUE.get(venue, venue), "year": year})
            continue
        tool, arg, claim = (m.group(3), m.group(4).strip(), m.group(5).strip())
        if tool == "arxiv":
            from .mcp import B
            valid_id = bool(re.fullmatch(r"\d{4}\.\d{4,5}", arg))
            found = B.verify(arg, claim) if valid_id else None
            v["checked"] += 1
            v["passed"] += bool(found)
            venue, year = found["edition"].rsplit("-", 1) if found else (None, None)
            segs.append({"t": "c", "paper_id": arg, "q": claim, "v": bool(found),
                         "title": found["title"] if found else arg,
                         "url": "https://arxiv.org/abs/" + arg if valid_id else None,
                         "venue": _VENUE.get(venue, venue), "year": year})
            continue
        fig = figures.check(tool, arg, claim, fcache)
        if fig["state"] != "na":                         # 'na' claims nothing
            v["fchecked"] += 1
            v["fpassed"] += fig["state"] == "ok"
        segs.append({"t": "n", "s": claim, "v": fig["state"], "tool": tool,
                     "arg": arg, "missing": fig.get("missing") or [],
                     "why": fig.get("why")})
    if pos < len(text):
        segs.append({"t": "p", "s": text[pos:]})
    return _auto_figures(segs, v, trail, fcache), v


def _auto_figures(segs: list, v: dict, trail, fcache: dict) -> list:
    """Check every printed number against this turn's own tool results.

    Numbers that hold up stay plain prose and are only counted; a number that
    is in no tool result and is not arithmetic over two of them is wrapped so
    the reader can see WHICH one it is. The mark states a fact — this number is
    not in what the tools returned — and accuses the sentence of nothing."""
    vals, used = figures.turn_pool(trail, fcache)
    if not vals:
        return segs
    deriv = figures.derived_set(vals)
    skip = {float(s["gid"]) for s in segs if s.get("t") == "c" and s.get("gid")}
    out: list = []
    for seg in segs:
        if seg.get("t") != "p":
            out.append(seg)
            continue
        text, pos = seg["s"], 0
        for a, b, raw, verdict in figures.scan(text, vals, deriv, skip):
            v["fchecked"] += 1
            v["fpassed"] += verdict != "no"
            if verdict != "no":
                continue
            if a > pos:
                out.append({"t": "p", "s": text[pos:a]})
            out.append({"t": "n", "s": raw, "v": "no", "auto": True,
                        "tool": ", ".join(used[:2]) or "this turn's tools",
                        "arg": "", "missing": [raw]})
            pos = b
        out.append({"t": "p", "s": text[pos:]})
    return out


_STORE: Store | None = None
_VER: Verifier | None = None


def _env():
    global _STORE, _VER
    if _STORE is None:
        _STORE = Store()
        _VER = Verifier(_STORE)
    return _STORE, _VER


def handle(body: dict) -> dict:
    """POST /chat entry point. {q, sid?} -> {segs, sid, verified}."""
    store, ver = _env()
    q = (body.get("q") or "").strip()
    if not q:
        return {"error": "empty question"}
    r = ask(q, body.get("sid") or None)
    if "error" in r:
        return r
    segs, v = segment(r["text"], store, ver)
    return {"segs": segs, "sid": r["sid"], "verified": v}


# ------------------------------------------------------------- agents
# Like orx: the machine's installed, signed-in agent CLIs are what "connect".

def _agent_status(name: str) -> dict:
    import shutil
    path = shutil.which(name) or (
        str(Path.home() / ".local/bin" / name)
        if (Path.home() / ".local/bin" / name).exists() else None)
    result = {"installed": bool(path), "authenticated": None,
              "status": "unknown" if path else "not_installed",
              "login_command": "codex login --device-auth" if name == "codex" else "claude auth login"}
    if not path:
        return result
    cmd = [path, "login", "status"] if name == "codex" else [path, "auth", "status", "--json"]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=8,
                              stdin=subprocess.DEVNULL, cwd=ROOT)
        if name == "claude":
            state = json.loads(done.stdout)
            connected = state.get("loggedIn")
            if isinstance(connected, bool):
                result["authenticated"] = connected
        else:
            # Only recognize documented status responses. Errors and timeouts
            # are unknown, not proof the user has signed out.
            message = (done.stdout + done.stderr).lower()
            if done.returncode == 0 and "logged in" in message:
                result["authenticated"] = True
            elif "not logged in" in message:
                result["authenticated"] = False
        if result["authenticated"] is not None:
            result["status"] = "connected" if result["authenticated"] else "sign_in_required"
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    # Never read credential files or forward the CLI's raw output/tokens.
    return result


def agents() -> dict:
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(_agent_status, ("claude", "codex")))
    return dict(zip(("claude", "codex"), values))


def _codex_config_args() -> list[str]:
    # Per-process overrides preserve the user's global MCP configuration.
    values = {"mcp_servers.bellwether.command": sys.executable,
              "mcp_servers.bellwether.args": ["-m", "bellwether", "mcp"],
              "mcp_servers.bellwether.env": agent_environment(),
              "mcp_servers.bellwether.default_tools_approval_mode": "writes",
              "sandbox_mode": "read-only"}
    args = []
    for key, value in values.items():
        if isinstance(value, dict):
            literal = "{ " + ", ".join(k + " = " + json.dumps(v) for k, v in value.items()) + " }"
        else:
            literal = json.dumps(value)
        args += ["-c", key + "=" + literal]
    return args


def card(gid: int) -> dict:
    """GET /paper/<gid>: the highlight card's material, for inline display.

    The same three-colour passage the browse cards carry — the paper's own
    sentences (pink: why it was needed / yellow: what is new / blue: what it
    achieved), never rewritten here or anywhere."""
    store, _ = _env()
    r = store.rec(gid)
    if r is None:
        return {"error": f"no record for gid {gid}"}
    key, _eid = store.where(gid)
    venue, year = key.rsplit("-", 1)
    sp = store.span_entry(gid) or [None] * 8
    n, L, K, R = sp[0] or [], sp[1], sp[2], sp[3]
    topics = []
    try:
        for t in store.topics(key)["topics"]:
            if _eid in t.get("explicit", ()) or _eid in t.get("via_child", ()):
                topics.append(t["label"])
    except FileNotFoundError:
        pass
    return {"gid": gid, "title": r["title"],
            "venue": _VENUE.get(venue, venue), "year": year,
            "authors": (r.get("authors") or [])[:3],
            "n_authors": r.get("n_authors"),
            "area": r.get("area"), "subarea": r.get("subarea"),
            "L": L or None,
            "K": K or (n[0] if n else None),
            "R": R or None,
            "novelty": n, "topics": topics,
            "abstract": r.get("abstract")}


# ------------------------------------------------------------- conversations
# A conversation is OURS, keyed by a stable chat id; the agent CLI issues a
# NEW session id on every resume, so the latest one is stored inside the doc
# and never shown to the client.

def _doc_path(cid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", cid):
        raise ValueError("bad chat id")
    return CHAT_DIR / f"{cid}.json"


def delete_chat(cid: str) -> dict:
    _doc_path(cid).unlink()
    return {"ok": True}


def update_chat(cid: str, body: dict) -> dict:
    """Rename and/or publish. A published conversation is the brief's heir:
    the exploration that earned a place on the front screen."""
    p2 = _doc_path(cid)
    d = json.loads(p2.read_text())
    if body.get("title"):
        d["title"] = body["title"].strip()[:80] or d["title"]
    if "pub" in body:
        d["pub"] = bool(body["pub"])
    p2.write_text(json.dumps(d, ensure_ascii=False))
    return {"ok": True, "title": d["title"], "pub": d.get("pub", False)}


def list_chats() -> list[dict]:
    out = []
    if CHAT_DIR.exists():
        for f in CHAT_DIR.glob("*.json"):
            try:
                d = json.loads(f.read_text())
                out.append({"id": d["id"], "title": d["title"], "ts": d["ts"],
                            "pub": d.get("pub", False)})
            except (OSError, KeyError, json.JSONDecodeError):
                continue
    out.sort(key=lambda e: e["ts"], reverse=True)
    return out


def translate_chat(cid: str, lang: str = "ko") -> dict:
    """Render this conversation's prose into `lang`, once, and keep it.

    Quotes and figures are copied untouched — the verdicts on this page were
    reached against the English and cannot move. A segment whose figures do
    not survive translation stays English."""
    from . import translate
    if lang != "ko":
        return {"error": f"no renderer for {lang}"}
    if not translate.enabled():
        return {"error": "korean rendering is off (WNAI_KO=1 turns it on)"}
    if not translate.up():
        return {"error": "no translation engine", "engine": translate.ENDPOINT}
    p2 = _doc_path(cid)
    d = json.loads(p2.read_text())
    done = 0
    for t in d.get("turns", []):
        if t.get("ko"):
            continue
        ko = translate.turn(t.get("segs", []))
        if ko:
            t["ko"] = ko
            done += 1
    if done:
        p2.write_text(json.dumps(d, ensure_ascii=False))
    d.pop("sid", None)
    return d


def get_chat(cid: str) -> dict:
    d = json.loads(_doc_path(cid).read_text())
    d.pop("sid", None)                      # internal
    return d


# A generation the reader can call off. The agent CLI spawns children (the MCP
# server among them), so the run gets its own process group and the whole group
# is signalled — terminating the parent alone leaves the tools running.
_RUNS: dict[str, dict] = {}


def stop_run(run: str) -> dict:
    e = _RUNS.get(run)
    if e is None:
        return {"ok": False}
    e["stopped"] = True
    p = e.get("proc")
    if p is not None and p.poll() is None:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            p.terminate()
    return {"ok": True}


def _corpus_id(store) -> dict:
    u = store.union
    return {"papers": len(u.get("keys") or []),
            "corpora": list(u.get("corpora") or [])}


def _summ(tool_input: dict) -> str:
    for k in ("query", "topic", "benchmark", "venue", "edition", "gid"):
        if k in (tool_input or {}):
            return str(tool_input[k])[:60]
    return ""


def _cli_failure(code: int, stderr: str) -> str:
    """Useful failure classes without forwarding credentials or raw CLI logs."""
    message = stderr.lower()
    if "read-only file system" in message:
        return "The agent CLI could not initialize its local state because its directory is read-only."
    if "not logged in" in message or "authentication" in message or "unauthorized" in message:
        return "The agent CLI requires sign-in. Open Settings, sign in with the CLI, then refresh status."
    if "rate limit" in message or "usage limit" in message:
        return "The agent CLI reports a usage limit. Try again after the account's limit resets."
    return f"The agent CLI exited with status {code}. Check its login and MCP configuration in Settings."


def stream(body: dict, emit) -> None:
    """POST /chat/stream: emit({'t':'tool'|'done'|'error', ...}) as SSE.

    Tool calls surface live — the reader watches the agent walk the corpus —
    and the final text arrives verified, exactly like handle()."""
    store, ver = _env()
    q = (body.get("q") or "").strip()
    if not q:
        emit({"t": "error", "error": "empty question"})
        return
    cid = body.get("chat") or secrets.token_hex(6)
    agent = body.get("agent") or "codex"
    if agent not in ("codex", "claude"):
        emit({"t": "error", "error": "Choose codex or claude"})
        return
    doc = None
    if _doc_path(cid).exists():
        doc = json.loads(_doc_path(cid).read_text())
    sid = doc.get("sid") if doc else None
    # a conversation stays on the agent it started with — sessions don't port
    if doc and doc.get("agent") and doc["agent"] != agent:
        agent = doc["agent"]

    system = system_for(body.get("lang"))
    if agent == "codex":
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check"]
        if sid:
            cmd = ["codex", "exec", "resume", sid, "--json",
                   "--skip-git-repo-check"]
        cmd += _codex_config_args()
        cmd.append(system + "\n\nUSER QUESTION:\n" + q)
    else:
        cmd = ["claude", "-p", q, "--output-format", "stream-json", "--verbose",
               "--include-partial-messages",
               "--max-turns", "12",
               "--mcp-config", _mcp_config(), "--strict-mcp-config",
               "--allowedTools", "mcp__bellwether",
               "--append-system-prompt", system]
        if sid:
            cmd += ["--resume", sid]

    result_text, new_sid, failed = None, None, None
    trees: list[dict] = []
    trail: list[dict] = []
    run = str(body.get("run") or secrets.token_hex(6))
    entry = _RUNS[run] = {"stopped": False, "proc": None}

    def _tool_event(name: str, arg_map: dict) -> None:
        emit({"t": "tool", "name": name, "arg": _summ(arg_map)})
        # the trail is evidence about HOW the answer was reached, so it is
        # stored with the turn — until now it lived only in the open tab and
        # vanished when the conversation was reopened
        trail.append({"name": name, "arg": json.dumps(arg_map or {}, sort_keys=True)
                      if figures.RECOMPUTABLE.get(name) == "json" else _summ(arg_map)})
        # the derivation tree renders as a component, not prose: recompute the
        # same deterministic scan server-side and hand it to the page directly
        if name == "gap_scan" and (arg_map or {}).get("topic"):
            try:
                from .mcp import t_gap_scan
                tree = t_gap_scan({"topic": arg_map["topic"]})
                if "error" not in tree:
                    trees.append(tree)
                    emit({"t": "tree", "tree": tree})
            except Exception:  # noqa: BLE001 — the prose answer still lands
                pass

    try:
        with subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True,
                              stdin=subprocess.DEVNULL,
                              start_new_session=True) as p:
            entry["proc"] = p
            errors = deque(maxlen=12)

            def drain_errors():
                for line in p.stderr:
                    errors.append(line[-1000:])

            reader = threading.Thread(target=drain_errors, daemon=True)
            reader.start()
            for line in p.stdout:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if agent == "codex":
                    # measured event shape (codex 0.153): thread.started
                    # carries thread_id; item.completed carries typed items.
                    # An 'error' item is a WARNING (the run continues) — only
                    # turn.failed is fatal.
                    ty = ev.get("type") or ""
                    if ty == "thread.started":
                        new_sid = ev.get("thread_id") or new_sid
                    elif ty == "turn.failed":
                        failed = str(ev.get("error") or "codex turn failed")[:300]
                    elif ty in ("item.started", "item.completed"):
                        it = ev.get("item") or {}
                        ity = it.get("type") or ""
                        if "mcp" in ity and ty == "item.started":
                            _tool_event((it.get("tool") or it.get("name")
                                         or "tool").split("__")[-1],
                                        it.get("arguments")
                                        if isinstance(it.get("arguments"), dict)
                                        else {})
                        elif ity == "agent_message" and ty == "item.completed":
                            result_text = it.get("text") or result_text
                else:
                    if ev.get("type") == "stream_event":
                        e2 = ev.get("event") or {}
                        if e2.get("type") == "content_block_delta":
                            d2 = e2.get("delta") or {}
                            if d2.get("type") == "text_delta" and d2.get("text"):
                                emit({"t": "d", "s": d2["text"]})
                    elif ev.get("type") == "assistant":
                        for c in (ev.get("message") or {}).get("content", []):
                            # only corpus tools make the visible trail —
                            # harness plumbing is noise to the reader
                            if c.get("type") == "tool_use" \
                                    and c["name"].startswith("mcp__bellwether__"):
                                _tool_event(c["name"].split("__")[-1],
                                            c.get("input") or {})
                    elif ev.get("type") == "result":
                        result_text = ev.get("result") or ""
                        new_sid = ev.get("session_id")
                        if ev.get("is_error"):
                            failed = (result_text or "agent failed")[:300]
            code = p.wait()
            reader.join(timeout=1)
            if code and not failed:
                failed = _cli_failure(code, "".join(errors))
    except Exception as exc:  # noqa: BLE001
        emit({"t": "error", "error": f"{type(exc).__name__}: {exc}"[:300]})
        return
    finally:
        _RUNS.pop(run, None)
    if entry["stopped"]:
        # a half-finished answer has unverified anchors — it is not saved and
        # not shown as the paper's words
        emit({"t": "stopped"})
        return
    if failed or result_text is None:
        emit({"t": "error", "error": failed or "the agent returned nothing"})
        return
    segs, v = segment(result_text, store, ver, trail)
    turn = {"q": q, "segs": segs, "verified": v,
            # what it was answered against, so the same question can be put to
            # the same shelf later — the corpus is fixed, that is the point
            "on": _corpus_id(store)}
    if any(step["name"] in figures.RECOMPUTABLE and figures.RECOMPUTABLE[step["name"]] == "json" for step in trail):
        from .mcp import B
        current = B.data()
        turn["on"]["benchmarks"] = {k: current.get(k) for k in ("snapshot_id", "generated_at", "source", "available")}
    if trail:
        turn["trail"] = trail
    if trees:
        turn["trees"] = trees
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    if doc is None:
        doc = {"id": cid, "title": q[:80], "ts": int(time.time()),
               "agent": agent, "turns": []}
    doc["sid"] = new_sid or sid
    doc["ts"] = int(time.time())
    doc["turns"].append(turn)
    _doc_path(cid).write_text(json.dumps(doc, ensure_ascii=False))
    emit({"t": "done", "chat": cid, "segs": segs,
          "verified": turn["verified"]})
