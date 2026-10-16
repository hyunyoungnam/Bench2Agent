"""Locations of introduced benchmarks from their introducing papers' own text
(2026-10-16; rule-based extraction, existence checks, sampled audit — not a
full review).

A candidate is one (benchmark, link) pair whose OWN sentence in the
introducing paper both (a) releases something ("available", "released",
"download", …) about data or about the benchmark by name and (b) names the
benchmark or is a first-person release ("our …", "we release …"). Each pair
carries risk tags (unnamed sentence, paper introducing several benchmarks,
homonym entity, host kind) so an audit can be stratified; a candidate is not
a confirmed location.

Existence is checked apart from the official relation: GitHub through the
REST API with the dedicated token GITHUB_API_TOKEN read by name from `.env`
(sent only to api.github.com, never stored), Hugging Face through its public
API, project pages by an HTTP GET. Sequential, at most one request a second,
every answer cached, resumable; an authentication, rate-limit or network
failure is recorded as unchecked, never as "gone".

    python3 -m icml.paper_links candidates     # offline
    python3 -m icml.paper_links check          # network; resumable
    python3 -m icml.paper_links sample         # fixes 60 distinct pairs (refuses to redo)
    python3 -m icml.paper_links report

Outputs in the Git-ignored data/work/links/.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import random
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .common import ROOT, read_jsonl

OUT = ROOT / "data" / "work" / "links"
CANDIDATES = OUT / "candidates.jsonl"
CHECKS = OUT / "checks.jsonl"
SAMPLE = OUT / "audit_sample.json"
REVISION = "41f946529d84bd69"            # the candidate catalogue the pool is drawn from (installed, inactive)
SEED = 20261016
UA = "bench2agent-links/0.1 (research; benchmark locations)"
_SSL = ssl.create_default_context()
_SSL.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)

REL = re.compile(r"\b(available|release[sd]?|publicly|open-?source[sd]?|download|hosted|accessible|can be found)\b", re.I)
DATA = re.compile(r"\b(data|dataset|datasets|benchmark|corpus)\b", re.I)
# v2 (after the first audit's sentences): a link to someone else's data, a license or a cited source is not the
# benchmark's location; in a sentence with several links only the link whose own stretch names the benchmark or
# is a first-person release counts; leaderboards, visualizations, demos and evaluation tools are related pages
THIRD_PARTY = re.compile(r"\b(?:download(?:ed)?|obtain(?:ed)?|collect(?:ed)?|taken|sourced|adopted|built\s+upon|based\s+on)\b[^.]{0,60}\bfrom\b"
                         r"|licen[sc]e|/LICENSE|\btheir\s+(?:project|official|website|repositor)|access\s+restrictions|provided\s+by|\[\d+\]", re.I)
RELATED = re.compile(r"leaderboard|visuali[sz]ation|\bdemo\b|playground|evaluation\s+(?:protocol|tool|code|scripts?)|toolkit|\btools?\b", re.I)
OWN = re.compile(r"\b(our|we)\b[^.]{0,80}\b(code|data|dataset|benchmark|release|available|open-?source)"
                 r"|\b(code|data|dataset|benchmark)s?\b[^.]{0,40}\b(is|are)\s+(publicly\s+)?available", re.I)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- candidates (offline)

def cmd_candidates(rules: str = "v1") -> None:
    from .corpus import EDITIONS
    from .enrich_pilot import URL_RE, classify_url
    from .taxonomy import dataset_key
    from .update import edition_paths
    db = ROOT / "data/revisions" / REVISION / "benchmark_snapshot.sqlite"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    located = {r[0] for r in con.execute("SELECT id FROM benchmarks WHERE COALESCE(hf,'')!='' OR COALESCE(gh,'')!='' OR COALESCE(url,'')!='' "
                                         "OR extra LIKE '%reviewed_locations%'")}
    cat = json.loads((ROOT / "config/benchmarks_introduced.json").read_text(encoding="utf-8"))["benchmarks"]
    pool = [e for e in cat if e["claims"] and e["id"] not in located]
    per_paper = collections.Counter(c["arxiv_base"] for e in cat for c in e["claims"])
    need = collections.defaultdict(set)
    for e in pool:
        for c in e["claims"]:
            need[c["edition"]].add(c["arxiv_base"])
    text = {}
    for disp, year in EDITIONS:
        p = edition_paths(disp, year)
        if p["key"] in need and p["fulltext"].exists():
            for r in read_jsonl(p["fulltext"]):
                if r.get("ok") and r["arxiv_base"] in need[p["key"]]:
                    text[(p["key"], r["arxiv_base"])] = "\n".join(s.get("text", "") for s in r.get("sections") or [])
    OUT.mkdir(parents=True, exist_ok=True)
    n, seen = 0, set()
    target = CANDIDATES if rules == "v1" else OUT / f"candidates_{rules}.jsonl"
    with target.open("w", encoding="utf-8") as fh:
        for e in pool:
            names = {dataset_key(x) for x in [e["name"], *(e.get("aliases") or [])] if dataset_key(x)}
            for c in e["claims"]:
                t = text.get((c["edition"], c["arxiv_base"]))
                if not t:
                    continue
                for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", t):
                    links = [m for m in URL_RE.finditer(sent) if classify_url(m.group(0).rstrip(".,;:)]"))[0] in ("gh", "hfd", "hfm", "hfs", "project")]
                    for li, m in enumerate(URL_RE.finditer(sent)):
                        url = m.group(0).rstrip(".,;:)]")
                        kind, rid = classify_url(url)
                        if kind not in ("gh", "hfd", "hfm", "hfs", "project"):
                            continue
                        named = any(x in dataset_key(sent) for x in names)
                        own = bool(OWN.search(sent))
                        if not (REL.search(sent) and (DATA.search(sent) or named) and (named or own)):
                            continue                                 # both conditions, in THIS sentence, for THIS link
                        extra_tags = []
                        if rules == "v2":
                            prev_end = max([x.end() for x in links if x.end() <= m.start()], default=0)
                            next_start = min([x.start() for x in links if x.start() >= m.end()], default=len(sent))
                            stretch = sent[max(prev_end, m.start() - 160):m.start()]       # this link's own stretch: before it ...
                            around = stretch + " " + sent[m.end():min(next_start, m.end() + 120)]   # ... and up to the next link
                            if THIRD_PARTY.search(stretch):
                                continue                                 # someone else's data, a license, a cited source
                            if len(links) > 1:
                                if not (any(x in dataset_key(around) for x in names) or OWN.search(around) or DATA.search(around)):
                                    continue                             # several links: this one is not tied to the benchmark or its data
                                extra_tags.append("multi_link")
                            if kind == "hfm" or ((RELATED.search(around) or RELATED.search(url)) and not DATA.search(around)):
                                extra_tags.append("related_page")        # a model, a leaderboard, a demo, a tool: not the data
                        key = (e["id"], kind, rid if kind != "project" else url.lower().rstrip("/"))
                        if key in seen:
                            continue
                        seen.add(key)
                        tags = [("named" if named else "unnamed"), ("multi_intro_paper" if per_paper[c["arxiv_base"]] > 1 else "single_intro_paper"),
                                *(["homonym"] if e.get("homonym_of") else []), f"host:{kind}", *extra_tags]
                        fh.write(json.dumps({"pair": f"{e['id']}|{key[2]}", "benchmark_id": e["id"], "name": e["name"], "kind": kind,
                                             "id": rid if kind != "project" else None, "url": url, "paper_id": c["arxiv_base"],
                                             "edition": c["edition"], "paper_title": c.get("title"), "sentence": sent[:600], "tags": tags},
                                            ensure_ascii=False) + "\n")
                        n += 1
    rows = list(read_jsonl(target))
    print(f"[{rules}] pool {len(pool)} benchmarks without a location; candidate pairs {n} for {len({r['benchmark_id'] for r in rows})} benchmarks")
    print("tags:", dict(collections.Counter(t for r in rows for t in r["tags"])))
    print("distinct to check:", dict(collections.Counter(r["kind"] for r in {(r['kind'], r['id'] or r['url']): r for r in rows}.values())))


# ---------------------------------------------------------------- existence (network)

def _github_token() -> str | None:
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        k, sep, v = line.partition("=")
        if sep and k.strip() == "GITHUB_API_TOKEN":
            return v.strip().strip('"').strip("'") or None
    return None


class Halt(Exception):
    pass


def _get(url: str, headers: dict, max_bytes: int = 400_000) -> tuple[int | None, dict, bytes, str | None]:
    """(status, selected headers, body, error). status None = no answer (network)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **headers})
    keep = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Used", "X-RateLimit-Reset", "Retry-After", "Content-Type")
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
            return r.status, {h: r.headers.get(h) for h in keep if r.headers.get(h)}, r.read(max_bytes), None
    except urllib.error.HTTPError as e:
        return e.code, {h: e.headers.get(h) for h in keep if e.headers.get(h)}, e.read(2000) if e.fp else b"", None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, {}, b"", type(e).__name__


def check_github(repo: str, token: str) -> dict:
    st, hd, body, err = _get(f"https://api.github.com/repos/{repo}", {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                                                     "X-GitHub-Api-Version": "2022-11-28"})
    rec = {"status": st, "limits": {k: v for k, v in hd.items() if k.startswith("X-RateLimit") or k == "Retry-After"}, "checked_at": _now(), "method": "github api (token)"}
    if st is None:
        return {**rec, "existence": "unchecked", "why": f"no answer ({err})"}
    if st == 401:
        raise Halt("GitHub: 401 (the token was refused)")
    if st in (403, 429):
        text = body.decode("utf-8", "ignore").lower()
        if hd.get("X-RateLimit-Remaining") == "0" or "rate limit" in text or st == 429:
            raise Halt(f"GitHub: rate limit (status {st}, reset {hd.get('X-RateLimit-Reset')}, retry-after {hd.get('Retry-After')})")
        return {**rec, "existence": "unchecked", "why": f"HTTP {st}"}
    if st == 200:
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            return {**rec, "existence": "exists", "why": "metadata not parsed"}      # a 200 answer is the existence fact
        moved = d.get("full_name") and d["full_name"].lower() != repo.lower()
        return {**rec, "existence": "exists", "full_name": d.get("full_name"), "moved": bool(moved), "archived": d.get("archived"),
                "fork": d.get("fork"), "owner_type": (d.get("owner") or {}).get("type"), "pushed_at": d.get("pushed_at")}
    if st in (404, 451):
        return {**rec, "existence": "gone" if st == 404 else "unavailable (451)"}
    return {**rec, "existence": "unchecked", "why": f"HTTP {st}"}


def check_hub(kind: str, rid: str) -> dict:
    path = {"hfd": "datasets", "hfm": "models", "hfs": "spaces"}[kind]
    st, hd, body, err = _get(f"https://huggingface.co/api/{path}/{rid}", {})
    rec = {"status": st, "checked_at": _now(), "method": "hugging face api"}
    if st is None:
        return {**rec, "existence": "unchecked", "why": f"no answer ({err})"}
    if st == 200:
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            return {**rec, "existence": "exists", "why": "metadata not parsed (large listing)"}
        return {**rec, "existence": "exists", "gated": bool(d.get("gated")), "name": d.get("id")}
    if st == 401:
        return {**rec, "existence": "exists (gated or private to anonymous callers)"}
    if st == 404:
        return {**rec, "existence": "gone"}
    if st == 429:
        raise Halt("Hugging Face: rate limit")
    return {**rec, "existence": "unchecked", "why": f"HTTP {st}"}


def check_page(url: str) -> dict:
    st, hd, body, err = _get(url if url.startswith("http") else "https://" + url, {})
    rec = {"status": st, "checked_at": _now(), "method": "http get"}
    if st is None:
        return {**rec, "existence": "unchecked", "why": f"no answer ({err})"}
    if 200 <= st < 300:
        text = re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace"))
        return {**rec, "existence": "exists", "text": re.sub(r"\s+", " ", text)[:20000]}
    if st in (404, 410):
        return {**rec, "existence": "gone"}
    return {**rec, "existence": "unchecked", "why": f"HTTP {st}"}


def cmd_check(limit: int) -> None:
    token = _github_token()
    if not token:
        raise SystemExit("GITHUB_API_TOKEN is not set in .env")
    done = {r["key"] for r in read_jsonl(CHECKS)} if CHECKS.exists() else set()
    todo, seen = [], set()
    for r in read_jsonl(CANDIDATES):
        key = f"{r['kind']}:{r['id'] or r['url'].lower().rstrip('/')}"
        if key not in done and key not in seen:
            seen.add(key); todo.append((key, r))
    print(f"{len(todo)} to check ({len(done)} cached)", flush=True)
    n, t_last = 0, 0.0
    with CHECKS.open("a", encoding="utf-8") as fh:
        for key, r in todo[:limit]:
            wait = 1.0 - (time.time() - t_last)
            if wait > 0:
                time.sleep(wait)                             # at most one request a second, all hosts together
            t_last = time.time()
            try:
                res = check_github(r["id"], token) if r["kind"] == "gh" else check_hub(r["kind"], r["id"]) if r["kind"] in ("hfd", "hfm", "hfs") else check_page(r["url"])
            except Halt as e:
                print(f"stopped at {key}: {e}", flush=True)
                break
            except Exception as e:  # noqa: BLE001 — one item's failure is recorded, never ends the run
                res = {"existence": "unchecked", "why": f"{type(e).__name__}", "checked_at": _now()}
            fh.write(json.dumps({"key": key, **res}, ensure_ascii=False) + "\n"); fh.flush()
            n += 1
            if n % 100 == 0:
                lim = res.get("limits") or {}
                print(f"  {n} checked; github remaining {lim.get('X-RateLimit-Remaining')}", flush=True)
    rows = list(read_jsonl(CHECKS))
    print(f"checked this run {n}; recorded {len(rows)}:", dict(collections.Counter((r['key'].split(':')[0], r['existence']) for r in rows)))


# ---------------------------------------------------------------- audit sample

def cmd_sample(force: bool = False) -> None:
    if SAMPLE.exists() and not force:
        raise SystemExit(f"{SAMPLE} exists: the sample is fixed")
    rows = list(read_jsonl(CANDIDATES))
    rng = random.Random(SEED)
    rng.shuffle(rows)
    want = [("named", "single_intro_paper"), ("named", "multi_intro_paper"), ("unnamed", "single_intro_paper"),
            ("unnamed", "multi_intro_paper"), ("homonym",), ("host:project",), ("host:hfd",)]
    quota = {w: (10 if i < 4 else 7 if i == 4 else 7 if i == 5 else 6) for i, w in enumerate(want)}
    picked, used = [], set()
    for w in want:
        for r in rows:
            if sum(1 for p in picked if p["stratum"] == w) >= quota[w]:
                break
            if r["pair"] in used or not all(t in r["tags"] for t in w):
                continue
            used.add(r["pair"]); picked.append({**r, "stratum": w})
    for p in picked:
        p["stratum"] = "+".join(p["stratum"])
    SAMPLE.write_text(json.dumps({"fixed_at": _now(), "seed": SEED, "n": len(picked), "pairs": picked}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"sample {len(picked)} distinct pairs:", dict(collections.Counter(p["stratum"] for p in picked)))
    print("tags covered:", dict(collections.Counter(t for p in picked for t in p["tags"])))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["candidates", "check", "sample"])
    ap.add_argument("--limit", type=int, default=10 ** 6)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--rules", choices=["v1", "v2"], default="v1")
    a = ap.parse_args()
    if a.command == "candidates":
        cmd_candidates(a.rules)
    elif a.command == "check":
        cmd_check(a.limit)
    else:
        cmd_sample(a.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
