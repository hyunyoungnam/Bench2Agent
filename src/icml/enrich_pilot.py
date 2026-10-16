"""Pilot (2026-10-16): can OpenAlex and Tavily fill two gaps in the corpus?

  - papers on a conference list that are not linked to an arXiv record
    (OpenAlex: same paper? arXiv link? HTML our parser can read?);
  - introduced benchmarks with no confirmed location (Tavily: official
    repository / dataset / project page?), measured against a zero-cost
    baseline: the introducing paper's own parsed text.

A pilot, not a pipeline stage: fixed samples (50 papers, 50 benchmarks),
capped requests, every response cached, nothing in the registry, the match
files or a revision is touched. Outputs live in the Git-ignored
`data/work/pilot/`. Only OPENALEX_API_KEY and TAVILY_API_KEY are read from
`.env`, by name; both go in an `Authorization: Bearer` header, so no URL,
cache key, log line or report ever holds a key.

    python3 -m icml.enrich_pilot sample            # fix the samples once (refuses to redo)
    python3 -m icml.enrich_pilot baseline          # offline: links in the introducing papers' own text
    python3 -m icml.enrich_pilot openalex --limit 2
    python3 -m icml.enrich_pilot tavily --limit 2
    python3 -m icml.enrich_pilot checks            # GitHub/Hub existence, README text, arXiv HTML status
    python3 -m icml.enrich_pilot report

Caps (whole pilot, counted in the ledger, cache hits free): OpenAlex 100
network requests, Tavily 50 basic searches, Tavily Extract 10 URLs. An
authentication, budget or rate-limit error stops the service; nothing is
retried automatically. A request whose response never arrived is recorded
as `no_response` (it may have been billed).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import random
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .common import ROOT, read_jsonl

PILOT = ROOT / "data" / "work" / "pilot"
SAMPLE = PILOT / "sample.json"
CACHE = PILOT / "cache.jsonl"
LEDGER = PILOT / "ledger.jsonl"
BASELINE = PILOT / "baseline.json"
CHECKS = PILOT / "checks.json"
REVIEW = PILOT / "review.json"
REPORT = PILOT / "report.md"
CAPS = {"openalex": 100, "tavily_search": 50, "tavily_extract_urls": 10}
CANDIDATE_REVISION = "0cd3e7b4b800f054"     # the catalogue the benchmark sample is drawn from (installed, inactive)
ACTIVE_REVISION = "66c4aa1cc0c564e7"
SEED = 20261016
UA = "bench2agent-pilot/0.1 (research; benchmark discovery)"

_SSL = ssl.create_default_context()
_SSL.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)


class Stop(Exception):
    """A service said no (auth, budget, rate limit): stop it, do not retry."""


# ---------------------------------------------------------------- keys, cache, ledger

def _key(name: str) -> str | None:
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() == name:
            return v.strip().strip('"').strip("'") or None
    return None


def _scrub(text: str) -> str:
    for name in ("OPENALEX_API_KEY", "TAVILY_API_KEY"):
        k = _key(name)
        if k:
            text = text.replace(k, "***")
    return text


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _cache_key(service: str, endpoint: str, payload: dict) -> str:
    return hashlib.sha256(json.dumps([service, endpoint, payload], sort_keys=True).encode()).hexdigest()[:24]


def _cache() -> dict[str, dict]:
    return {r["k"]: r for r in read_jsonl(CACHE)} if CACHE.exists() else {}


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _sent(service: str) -> int:
    if not LEDGER.exists():
        return 0
    rows = list(read_jsonl(LEDGER))
    if service == "tavily_extract_urls":
        return sum(r.get("urls", 0) for r in rows if r["service"] == "tavily_extract" and r["event"] == "sent")
    return sum(1 for r in rows if r["service"] == service and r["event"] == "sent")


def request(service: str, endpoint: str, payload: dict, *, urls: int = 0) -> dict:
    """One capped, cached, ledgered call. `service`: openalex | tavily_search | tavily_extract."""
    ck = _cache_key(service, endpoint, payload)
    hit = _cache().get(ck)
    if hit:
        _append(LEDGER, {"service": service, "event": "cache_hit", "k": ck, "at": _now()})
        return hit["response"]
    cap_name = "tavily_extract_urls" if service == "tavily_extract" else service
    used = _sent(cap_name) + (urls if service == "tavily_extract" else 1)
    if used > CAPS[cap_name]:
        raise Stop(f"{cap_name} cap {CAPS[cap_name]} reached")
    if service == "openalex":
        key = _key("OPENALEX_API_KEY")
        url = "https://api.openalex.org/" + endpoint + ("?" + urllib.parse.urlencode(payload) if payload else "")
        req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Authorization": f"Bearer {key}"} if key else {})})
    else:
        key = _key("TAVILY_API_KEY")
        if not key:
            raise Stop("TAVILY_API_KEY is empty")
        req = urllib.request.Request("https://api.tavily.com/" + endpoint, data=json.dumps(payload).encode(), method="POST",
                                     headers={"User-Agent": UA, "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    _append(LEDGER, {"service": service, "event": "sent", "k": ck, "endpoint": endpoint.split("/")[0], "urls": urls, "at": _now()})
    try:
        with urllib.request.urlopen(req, timeout=60, context=_SSL) as r:
            body = json.loads(r.read().decode("utf-8"))
            usage = {h: r.headers.get(h) for h in ("X-RateLimit-Credits-Used", "X-RateLimit-Remaining", "X-RateLimit-Limit") if r.headers.get(h)}
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
        _append(LEDGER, {"service": service, "event": "http_error", "k": ck, "status": status, "at": _now()})
        if status in (401, 402, 403, 429, 432, 433):
            raise Stop(f"{service}: HTTP {status}") from None
        if status == 404:
            body = {"_status": 404}
            _append(CACHE, {"k": ck, "service": service, "endpoint": endpoint, "payload": payload, "response": body, "at": _now()})
            return body
        raise Stop(f"{service}: HTTP {status}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _append(LEDGER, {"service": service, "event": "no_response", "k": ck, "error": _scrub(type(e).__name__), "at": _now()})
        raise Stop(f"{service}: no response ({type(e).__name__}); may have been billed") from None
    if service == "openalex":
        usage["cost_usd"] = (body.get("meta") or {}).get("cost_usd")
    else:
        usage["credits"] = (body.get("usage") or {}).get("credits")
    _append(LEDGER, {"service": service, "event": "ok", "k": ck, "status": status, "usage": usage, "at": _now()})
    _append(CACHE, {"k": ck, "service": service, "endpoint": endpoint, "payload": payload, "response": body, "usage": usage, "at": _now()})
    return body


# ---------------------------------------------------------------- shared helpers

def _sha(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _person(n: str) -> str:
    from .arxiv_match import _person as p
    return p(n)


def author_match(a: list[str], b: list[str]) -> dict:
    """A review aid, never an approval rule: authors paired one to one (each
    author of either list used once), full normalized names first, then
    surname + first initial. Both list sizes are reported, so no denominator
    is chosen for the reader."""
    P = lambda n: _person(n).split()
    pa, pb = [P(x) for x in a], [P(y) for y in b]
    left, used, full, initial = [i for i in range(len(a)) if pa[i]], set(), 0, 0
    for i in list(left):
        j = next((j for j in range(len(b)) if j not in used and pb[j] and pa[i] == pb[j]), None)
        if j is not None:
            used.add(j); left.remove(i); full += 1
    for i in list(left):
        j = next((j for j in range(len(b)) if j not in used and pb[j] and pa[i][-1] == pb[j][-1] and pa[i][0][0] == pb[j][0][0]), None)
        if j is not None:
            used.add(j); left.remove(i); initial += 1
    return {"conference": len(a), "candidate": len(b), "full": full, "initial_only": initial}


def author_overlap(a: list[str], b: list[str]) -> tuple[int, int]:
    """(full + initial-only one-to-one matches, len(a)); kept for the sample-time display."""
    m = author_match(a, b)
    return m["full"] + m["initial_only"], m["conference"]


def jaccard(x: str, y: str) -> float:
    from .arxiv_match import toks
    a, b = toks(x), toks(y)
    return len(a & b) / len(a | b) if a and b else 0.0


def _editions():
    from .corpus import EDITIONS
    from .update import edition_paths
    for disp, year in EDITIONS:
        yield edition_paths(disp, year)


def _family(key: str) -> str:
    v = key.split("-")[0]
    return {"icml": "openreview", "iclr": "openreview", "neurips": "openreview", "cvpr": "cvf", "iccv": "cvf", "eccv": "ecva",
            "acl": "anthology", "emnlp": "anthology", "naacl": "anthology", "aaai": "ojs", "corl": "pmlr"}.get(v, v)


def anthology_doi(paper: dict) -> str | None:
    """ACL Anthology papers have DOIs 10.18653/v1/<anthology id> (an official id, by the Anthology's own rule)."""
    m = re.match(r"https?://aclanthology\.org/([0-9]{4}\.[a-z0-9-]+\.[0-9]+)/?$", paper.get("paper_url") or "")
    return f"10.18653/v1/{m.group(1)}" if m else None


# ---------------------------------------------------------------- sample

def _index():
    from .arxiv_match import toks
    newest: dict[str, dict] = {}
    for r in read_jsonl(ROOT / "data/raw/arxiv/oai_index.jsonl"):
        s = r.get("datestamp") or r.get("date") or ""
        if r["arxiv_base"] not in newest or s >= (newest[r["arxiv_base"]].get("datestamp") or newest[r["arxiv_base"]].get("date") or ""):
            newest[r["arxiv_base"]] = r
    rows = list(newest.values())
    postings: dict[str, list[int]] = collections.defaultdict(list)
    for i, r in enumerate(rows):
        for t in toks(r["title"]):
            postings[t].append(i)
    return rows, postings


def best_overlap(title: str, rows, postings) -> tuple[float, dict | None]:
    from .arxiv_match import toks
    pt = toks(title)
    if not pt:
        return 0.0, None
    counts: dict[int, int] = collections.defaultdict(int)
    for t in pt:
        for i in postings.get(t, ()):
            counts[i] += 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:200]
    best, bi = 0.0, None
    for i, _ in top:
        j = jaccard(title, rows[i]["title"])
        if j > best:
            best, bi = j, i
    return round(best, 3), (rows[bi] if bi is not None else None)


def cmd_sample(force: bool = False) -> None:
    if SAMPLE.exists() and not force:
        raise SystemExit(f"{SAMPLE} exists: the samples are fixed (use --force only to rebuild a broken file)")
    rng = random.Random(SEED)
    print("loading the arXiv index ...", flush=True)
    rows, postings = _index()
    inputs = {"oai_index.jsonl": _sha(ROOT / "data/raw/arxiv/oai_index.jsonl")}
    special, pool = [], collections.defaultdict(list)
    for paths in _editions():
        if not (paths["papers"].exists() and paths["resolved"].exists()):
            continue
        inputs[paths["papers"].name] = _sha(paths["papers"]); inputs[paths["resolved"].name] = _sha(paths["resolved"])
        papers = {p["event_id"]: p for p in read_jsonl(paths["papers"])}
        for r in read_jsonl(paths["resolved"]):
            if r.get("arxiv_base"):
                continue
            p = papers.get(r["event_id"])
            if not p:
                continue
            item = {"edition": paths["key"], "event_id": p["event_id"], "title": p["title"], "authors": p.get("authors") or [],
                    "venue": p.get("venue"), "year": p.get("year"), "paper_url": p.get("paper_url"),
                    "openreview_id": p.get("openreview_id"), "source_ids": p.get("source_ids"), "match": r.get("match") or "none",
                    "stored_score": r.get("score"), "family": _family(paths["key"]), "doi": anthology_doi(p),
                    "input": {"papers": paths["papers"].name, "resolved": paths["resolved"].name}}
            if item["match"] in ("ambiguous", "collision"):
                special.append(item)
            elif paths["key"] == "iclr-2025" and p["event_id"] == 31893:
                special.append(item)
            else:
                pool[item["family"]].append(item)
    # fixed pre-sample per venue family, then the recomputed best overlap decides the band
    bands = {"near (0.70-0.85)": [], "mid (0.50-0.70)": [], "low (<0.50)": []}
    for fam in sorted(pool):
        cand = rng.sample(pool[fam], min(80, len(pool[fam])))
        for it in cand:
            j, hit = best_overlap(it["title"], rows, postings)
            it["best_overlap"], it["best_arxiv"] = j, ({"arxiv_base": hit["arxiv_base"], "title": hit["title"]} if hit else None)
            band = "near (0.70-0.85)" if 0.70 <= j < 0.85 else "mid (0.50-0.70)" if 0.50 <= j < 0.70 else "low (<0.50)" if j < 0.50 else None
            if band:
                bands[band].append(it)
    papers = []
    for it in special:
        j, hit = best_overlap(it["title"], rows, postings)
        it["best_overlap"], it["best_arxiv"] = j, ({"arxiv_base": hit["arxiv_base"], "title": hit["title"]} if hit else None)
        ctrl = it["edition"] == "iclr-2025" and it["event_id"] == 31893
        papers.append({**it, "id": f"{it['edition']}:{it['event_id']}", "control": ctrl,
                       "reason": "control: Noisy MNIST's paper, the arXiv record is known (2410.02942)" if ctrl else f"failure type {it['match']}"})
    want = {"near (0.70-0.85)": 14, "mid (0.50-0.70)": 15, "low (<0.50)": 15}
    for band, items in bands.items():
        by_fam = collections.defaultdict(list)
        for it in items:
            by_fam[it["family"]].append(it)
        picked, fams = [], sorted(by_fam)
        while len(picked) < want[band] and any(by_fam.values()):          # round robin over venue families
            for f in fams:
                if by_fam[f] and len(picked) < want[band]:
                    picked.append(by_fam[f].pop(0))
        papers += [{**it, "id": f"{it['edition']}:{it['event_id']}", "control": False, "reason": f"unmatched, best title overlap {band}"} for it in picked]

    # benchmarks: introduced entities with no confirmed location in the candidate revision
    import sqlite3
    db = ROOT / "data/revisions" / CANDIDATE_REVISION / "benchmark_snapshot.sqlite"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    located = {r[0] for r in con.execute("SELECT id FROM benchmarks WHERE COALESCE(hf,'')!='' OR COALESCE(gh,'')!='' OR COALESCE(url,'')!=''")}
    locs = {r[0]: {"hf": r[1], "gh": r[2], "url": r[3]} for r in con.execute("SELECT id, hf, gh, url FROM benchmarks")}
    cat = json.loads((ROOT / "config/benchmarks_introduced.json").read_text(encoding="utf-8"))["benchmarks"]
    inputs["benchmarks_introduced.json"] = _sha(ROOT / "config/benchmarks_introduced.json")
    inputs[f"revision {CANDIDATE_REVISION}"] = _sha(db)

    def bitem(e, reason, control=False):
        c = e["claims"][0]
        return {"id": e["id"], "name": e["name"], "kind": e.get("kind"), "aliases": e.get("aliases"), "homonym_of": e.get("homonym_of"),
                "registered": e.get("registered"), "first_claim": e.get("first_claim"), "developers": e.get("developers") or [],
                "introducing_paper": {"arxiv_base": c["arxiv_base"], "edition": c["edition"], "title": c["title"], "claim_id": c["claim_id"],
                                      "evidence": c["evidence"], "label": c.get("label")},
                "existing_location": locs.get(e["id"]) if control else None, "control": control, "reason": reason, "family": _family(c["edition"])}
    no_loc = [e for e in cat if e["claims"] and e["id"] not in located and e["claims"][0].get("title")]
    homonyms = [e for e in no_loc if e.get("homonym_of")]
    others = [e for e in no_loc if not e.get("homonym_of")]
    rng.shuffle(homonyms); rng.shuffle(others)
    bench = [bitem(e, "no confirmed location; shares its name with another entity") for e in homonyms[:10]]
    by_fam = collections.defaultdict(list)
    for e in others:
        by_fam[_family(e["claims"][0]["edition"])].append(e)
    fams, picked = sorted(by_fam), []
    while len(picked) < 35:
        for f in fams:
            if by_fam[f] and len(picked) < 35:
                picked.append(by_fam[f].pop(0))
    bench += [bitem(e, "no confirmed location") for e in picked]
    controls = [e for e in cat if e["claims"] and e["id"] in located and e["claims"][0].get("title")]
    rng.shuffle(controls)
    bench += [bitem(e, "control: a location is already recorded", control=True) for e in controls[:5]]
    out = {"fixed_at": _now(), "seed": SEED, "active_revision": ACTIVE_REVISION, "candidate_revision": CANDIDATE_REVISION,
           "sources": {"papers": "working files (normalized lists, arXiv match files, OAI index) — unmatched papers are not in any revision",
                       "benchmarks": f"config/benchmarks_introduced.json as built into revision {CANDIDATE_REVISION} (installed, inactive)"},
           "input_sha256": inputs, "papers": papers, "benchmarks": bench}
    PILOT.mkdir(parents=True, exist_ok=True)
    SAMPLE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"papers {len(papers)}: " + str(collections.Counter(p['reason'].split(',')[0] if not p['reason'].startswith('unmatched') else p['reason'] for p in papers)))
    print("  by family:", dict(collections.Counter(p["family"] for p in papers)), "| with an official DOI:", sum(1 for p in papers if p["doi"]))
    print(f"benchmarks {len(bench)}: homonyms {sum(1 for b in bench if b['homonym_of'])}, controls {sum(1 for b in bench if b['control'])}, "
          f"kinds {dict(collections.Counter(b['kind'] for b in bench))}, families {dict(collections.Counter(b['family'] for b in bench))}")


def load_sample() -> dict:
    if not SAMPLE.exists():
        raise SystemExit("no sample: run `sample` first")
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- offline baseline

URL_RE = re.compile(r"https?://[^\s\)\]\}>\"'，。]+|(?<![\w/.])(?:github\.com|huggingface\.co)/[^\s\)\]\}>\"'，。]+", re.I)


def classify_url(url: str) -> tuple[str, str | None]:
    """(host kind, normalized id) for a URL: gh owner/repo, hfd/hfm/hfs id, or other."""
    u = url.rstrip(".,;:").replace("http://", "https://")
    if not u.startswith("http"):
        u = "https://" + u
    p = urllib.parse.urlsplit(u)
    host, parts = p.netloc.lower().removeprefix("www."), [x for x in p.path.split("/") if x]
    if host == "github.com" and len(parts) >= 2:
        return "gh", f"{parts[0]}/{parts[1].removesuffix('.git')}"
    if host == "huggingface.co" and parts:
        if parts[0] in ("datasets", "spaces") and len(parts) >= 3:
            return ("hfd" if parts[0] == "datasets" else "hfs"), f"{parts[1]}/{parts[2]}"
        if len(parts) >= 2 and parts[0] not in ("papers", "docs", "blog"):
            return "hfm", f"{parts[0]}/{parts[1]}"
    if host.endswith("github.io"):
        return "project", u
    if host in ("arxiv.org", "doi.org", "openreview.net", "aclanthology.org", "paperswithcode.com", "scholar.google.com"):
        return "paper", u
    return "other", u


def cmd_baseline() -> None:
    from .taxonomy import dataset_key
    s = load_sample()
    by_ed = collections.defaultdict(set)
    for b in s["benchmarks"]:
        by_ed[b["introducing_paper"]["edition"]].add(b["introducing_paper"]["arxiv_base"])
    text = {}
    for paths in _editions():
        if paths["key"] in by_ed and paths["fulltext"].exists():
            for r in read_jsonl(paths["fulltext"]):
                if r["arxiv_base"] in by_ed[paths["key"]] and r.get("ok"):
                    text[(paths["key"], r["arxiv_base"])] = "\n".join(sec.get("text", "") for sec in r.get("sections") or [])
    out = {}
    for b in s["benchmarks"]:
        ip = b["introducing_paper"]; t = text.get((ip["edition"], ip["arxiv_base"]))
        names = {dataset_key(n) for n in [b["name"], *(b.get("aliases") or [])] if dataset_key(n)}
        found = []
        if t:
            for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", t):
                for m in URL_RE.finditer(sent):
                    kind, rid = classify_url(m.group(0))
                    if kind == "paper":
                        continue
                    folded = dataset_key(sent)
                    found.append({"url": m.group(0).rstrip(".,;:"), "kind": kind, "id": rid, "sentence": sent[:400],
                                  "names_benchmark": any(n in folded for n in names),
                                  "release_words": bool(re.search(r"\b(available|release[sd]?|publicly|open-?source[sd]?|code|data(set)?|benchmark|download)\b", sent, re.I))})
        out[b["id"]] = {"paper_text": bool(t), "links": found}
    BASELINE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    n_text = sum(1 for v in out.values() if v["paper_text"])
    n_any = sum(1 for v in out.values() if v["links"])
    n_named = sum(1 for v in out.values() if any(l["names_benchmark"] and l["kind"] in ("gh", "hfd", "hfm", "hfs", "project") for l in v["links"]))
    print(f"benchmarks {len(out)}: introducing paper text {n_text}, any non-paper link {n_any}, repo/dataset/project link in a sentence naming the benchmark {n_named}")


# ---------------------------------------------------------------- OpenAlex

SELECT = "id,doi,display_name,publication_year,authorships,primary_location,locations,ids,best_oa_location,type"


def _clean_title(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[,:|!?()\[\]{}\"']", " ", t)).strip()


def _prefix_title(t: str, n: int = 6) -> str:
    """The first `n` distinctive words: OpenAlex's title.search needs every word, so a retitled
    version (a word added or swapped late in the title) is found by its opening words only."""
    from .arxiv_match import toks
    words = [w for w in _clean_title(t).split() if w.lower() in toks(w)]
    return " ".join(words[:n])


def openalex_arxiv(work: dict) -> str | None:
    for loc in work.get("locations") or []:
        for f in ("landing_page_url", "pdf_url"):
            m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", loc.get(f) or "")
            if m:
                return m.group(1)
    m = re.search(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})", (work.get("doi") or "").lower())
    return m.group(1) if m else None


def cmd_openalex(limit: int) -> None:
    s = load_sample()
    done = 0
    for p in s["papers"]:
        if done >= limit:
            break
        try:
            hits, how = [], []
            if p.get("doi"):
                w = request("openalex", f"works/doi:{p['doi']}", {"select": SELECT})
                how.append("doi")
                if w.get("_status") != 404 and w.get("id"):
                    hits.append(w)
            if not hits:
                r = request("openalex", "works", {"filter": f"title.search:{_clean_title(p['title'])}", "per-page": 5, "select": SELECT})
                how.append("title")
                hits = r.get("results") or []
            if not hits and _prefix_title(p["title"]) and _prefix_title(p["title"]) != _clean_title(p["title"]):
                r = request("openalex", "works", {"filter": f"title.search:{_prefix_title(p['title'])}", "per-page": 5, "select": SELECT})
                how.append("title-prefix")
                hits = r.get("results") or []
        except Stop as e:
            print(f"stopped at {p['id']}: {e}")
            break
        done += 1
        print(f"{p['id']}: {'/'.join(how)} -> {len(hits)} candidates")


def _arxiv_batch_payload(bases: list[str]) -> dict:
    return {"filter": "doi:" + "|".join(f"10.48550/arxiv.{b}" for b in sorted(bases)), "per-page": 50, "select": SELECT}


def cmd_openalex_arxiv() -> None:
    """The offline best arXiv candidate of each paper (overlap >= 0.5) looked up by its
    official arXiv DataCite DOI, in one batched request: an identifier, not a title, so the
    authors can be compared. Only candidates the title search did not already return."""
    s = load_sample()
    bases = sorted({(p.get("best_arxiv") or {}).get("arxiv_base") for p in s["papers"]
                    if (p.get("best_arxiv") or {}).get("arxiv_base") and (p.get("best_overlap") or 0) >= 0.5
                    and not any(c["arxiv"] == p["best_arxiv"]["arxiv_base"] for c in openalex_candidates(p))})
    if not bases:
        print("nothing to look up"); return
    r = request("openalex", "works", _arxiv_batch_payload(bases))
    PILOT.joinpath("openalex_arxiv_batch.json").write_text(json.dumps({"bases": bases}, indent=1))
    print(f"{len(bases)} arXiv DOIs -> {len(r.get('results') or [])} works")


def openalex_candidates(p: dict) -> list[dict]:
    cache = {r["k"]: r for r in _cache().values()}
    hits = []
    if p.get("doi"):
        r = cache.get(_cache_key("openalex", f"works/doi:{p['doi']}", {"select": SELECT}))
        if r and r["response"].get("id"):
            hits.append(("doi", r["response"]))
    r = cache.get(_cache_key("openalex", "works", {"filter": f"title.search:{_clean_title(p['title'])}", "per-page": 5, "select": SELECT}))
    if r:
        hits += [("title", w) for w in r["response"].get("results") or []]
    r = cache.get(_cache_key("openalex", "works", {"filter": f"title.search:{_prefix_title(p['title'])}", "per-page": 5, "select": SELECT}))
    if r and _prefix_title(p["title"]) != _clean_title(p["title"]):
        hits += [("title-prefix", w) for w in r["response"].get("results") or []]
    batch = PILOT / "openalex_arxiv_batch.json"
    ba = (p.get("best_arxiv") or {}).get("arxiv_base")
    if batch.exists() and ba:
        r = cache.get(_cache_key("openalex", "works", _arxiv_batch_payload(json.loads(batch.read_text())["bases"])))
        if r:
            hits += [("arxiv-doi of the offline candidate", w) for w in r["response"].get("results") or [] if openalex_arxiv(w) == ba]
    out = []
    for how, w in hits:
        authors = [a["author"]["display_name"] for a in w.get("authorships") or [] if a.get("author")]
        shared, of = author_overlap(p["authors"], authors)
        out.append({"via": how, "openalex": w.get("id"), "doi": w.get("doi"), "title": w.get("display_name"), "year": w.get("publication_year"),
                    "type": w.get("type"), "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
                    "authors": authors[:12], "title_overlap": round(jaccard(p["title"], w.get("display_name") or ""), 3),
                    "authors_shared": f"{shared}/{of}", "author_match": author_match(p["authors"], authors), "arxiv": openalex_arxiv(w),
                    "locations": [{k: l.get(k) for k in ("landing_page_url", "pdf_url", "version")} | {"source": (l.get("source") or {}).get("display_name")}
                                  for l in (w.get("locations") or [])][:8]})
    return out


# ---------------------------------------------------------------- arXiv's own metadata (free)

def cmd_arxiv_authors() -> None:
    """Authors, title and version of the candidate arXiv records from arXiv's
    API (`id_list`, one request, no key): the same 23 ids the OpenAlex
    arXiv-DOI batch asked for, plus the recovered ids that came from OpenAlex
    title search, so every recovered link can be compared. Cached; the
    returned ids are checked against the requested ones."""
    import xml.etree.ElementTree as ET
    s = load_sample()
    rev = json.loads(REVIEW.read_text(encoding="utf-8"))["papers"] if REVIEW.exists() else {}
    batch = json.loads((PILOT / "openalex_arxiv_batch.json").read_text())["bases"]
    extra = sorted({v["arxiv_id"] for v in rev.values() if v.get("arxiv_id")} - set(batch))
    ids = batch + extra
    out = PILOT / "arxiv_api.json"
    if out.exists():
        print("cached:", out); return
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": ",".join(ids), "max_results": len(ids)})
    _append(LEDGER, {"service": "arxiv", "event": "sent", "urls": len(ids), "at": _now()})
    st, body = _get_text(url)
    _append(LEDGER, {"service": "arxiv", "event": "ok" if st == 200 else "http_error", "status": st, "at": _now()})
    if st != 200:
        raise SystemExit(f"arXiv API: HTTP {st}")
    A = "{http://www.w3.org/2005/Atom}"
    got = {}
    for e in ET.fromstring(body).iter(f"{A}entry"):
        m = re.search(r"abs/(\d{4}\.\d{4,5})(v\d+)?", e.findtext(f"{A}id") or "")
        if not m:
            continue
        got[m.group(1)] = {"version": m.group(2), "title": re.sub(r"\s+", " ", e.findtext(f"{A}title") or "").strip(),
                           "authors": [a.findtext(f"{A}name") for a in e.findall(f"{A}author")],
                           "updated": e.findtext(f"{A}updated"), "published": e.findtext(f"{A}published")}
    out.write_text(json.dumps({"requested": ids, "from_openalex_batch": batch, "added_title_search_recoveries": extra,
                               "fetched_at": _now(), "missing": sorted(set(ids) - set(got)), "records": got}, ensure_ascii=False, indent=1))
    print(f"requested {len(ids)} (23 from the OpenAlex batch + {len(extra)} title-search recoveries), returned {len(got)}, missing {sorted(set(ids) - set(got))}")


# ---------------------------------------------------------------- Tavily

def tavily_query(b: dict) -> str:
    return f"\"{b['name']}\" {b['introducing_paper']['title'][:180]}"


def cmd_tavily(limit: int) -> None:
    s = load_sample()
    done = 0
    for b in s["benchmarks"]:
        if done >= limit:
            break
        try:
            r = request("tavily_search", "search", {"query": tavily_query(b), "search_depth": "basic", "max_results": 10, "include_usage": True})
        except Stop as e:
            print(f"stopped at {b['id']}: {e}")
            break
        done += 1
        print(f"{b['id']}: {len(r.get('results') or [])} results, credits {(r.get('usage') or {}).get('credits')}")


def tavily_results(b: dict) -> list[dict]:
    r = _cache().get(_cache_key("tavily_search", "search", {"query": tavily_query(b), "search_depth": "basic", "max_results": 10, "include_usage": True}))
    if not r:
        return []
    out = []
    for x in r["response"].get("results") or []:
        kind, rid = classify_url(x.get("url") or "")
        out.append({"url": x.get("url"), "kind": kind, "id": rid, "title": x.get("title"), "snippet": (x.get("content") or "")[:300], "score": x.get("score")})
    return out


def cmd_extract(urls: list[str]) -> None:
    r = request("tavily_extract", "extract", {"urls": urls, "extract_depth": "basic", "include_usage": True}, urls=len(urls))
    print(f"extracted {len(r.get('results') or [])}, failed {len(r.get('failed_results') or [])}, credits {(r.get('usage') or {}).get('credits')}")


# ---------------------------------------------------------------- checks (free public endpoints)

def _get_text(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
            raw = r.read(400_000)
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, ""


def cmd_checks() -> None:
    """Existence (GitHub/Hub API, no token: the registry's own `fetch_meta`),
    README / dataset-card text (raw files, for the official-relation check),
    and arXiv HTML status for arXiv ids OpenAlex offers. Cached per key."""
    from .resources import RateLimited, fetch_meta
    s = load_sample()
    checks = json.loads(CHECKS.read_text(encoding="utf-8")) if CHECKS.exists() else {}
    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    keys = set()
    for b in s["benchmarks"]:
        for l in (base.get(b["id"]) or {}).get("links") or []:
            if l["kind"] in ("gh", "hfd", "hfm", "hfs"):
                keys.add((l["kind"], l["id"]))
        for x in tavily_results(b):
            if x["kind"] in ("gh", "hfd", "hfm", "hfs"):
                keys.add((x["kind"], x["id"]))
    gh_limited = False
    for kind, rid in sorted(keys):
        k = f"{kind}:{rid}"
        if k in checks:
            continue
        if kind == "gh" and gh_limited:
            continue
        try:
            meta = fetch_meta(kind, rid, {})
        except RateLimited:
            if kind == "gh":
                gh_limited = True
                print("GitHub API limit reached (no token is used): remaining repos unchecked")
            continue
        readme = ""
        if meta and not meta.get("gone"):
            if kind == "gh":
                for name in ("README.md", "readme.md", "README.rst", "README"):
                    st, readme = _get_text(f"https://raw.githubusercontent.com/{rid}/HEAD/{name}")
                    if st == 200:
                        break
            else:
                path = {"hfd": "datasets/", "hfs": "spaces/", "hfm": ""}[kind]
                st, readme = _get_text(f"https://huggingface.co/{path}{rid}/raw/main/README.md")
                readme = readme if st == 200 else ""
        checks[k] = {"meta": meta, "checked_at": _now(), "method": "api", "readme": readme[:20000]}
        CHECKS.write_text(json.dumps(checks, ensure_ascii=False, indent=1), encoding="utf-8")
    for p in s["papers"]:
        for c in openalex_candidates(p):
            if c["arxiv"] and f"arxiv-html:{c['arxiv']}" not in checks:
                st, body = _get_text(f"https://arxiv.org/html/{c['arxiv']}")
                checks[f"arxiv-html:{c['arxiv']}"] = {"status": st, "ltx": "ltx_page" in body or "ltx_document" in body, "checked_at": _now()}
                CHECKS.write_text(json.dumps(checks, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"checks recorded: {len(checks)}")


# ---------------------------------------------------------------- report

def cmd_report() -> None:
    s = load_sample()
    rev = json.loads(REVIEW.read_text(encoding="utf-8")) if REVIEW.exists() else {"papers": {}, "benchmarks": {}}
    ledger = list(read_jsonl(LEDGER)) if LEDGER.exists() else []
    lines = ["# OpenAlex / Tavily pilot", "", f"Samples fixed {s['fixed_at']} (seed {s['seed']}); benchmark catalogue from revision {s['candidate_revision']};",
             f"active revision {s['active_revision']} unchanged. Reviewer: Claude Code (AI review), not human review.", ""]
    P = [p for p in s["papers"] if not p["control"]]
    pr = {p["id"]: rev["papers"].get(p["id"], {}) for p in P}
    via = collections.Counter()
    for p in P:
        v = pr[p["id"]]
        if v.get("arxiv") == "confirmed":
            c = next((c for c in openalex_candidates(p) if c["arxiv"] == v.get("arxiv_id")), {})
            via["the offline candidate, confirmed by its OpenAlex record (arXiv DOI lookup)" if c.get("via", "").startswith("arxiv-doi") else "OpenAlex title search"] += 1
    by_band = collections.Counter(p["reason"].split(",")[-1].strip() for p in P if pr[p["id"]].get("arxiv") == "confirmed")
    lines += ["## OpenAlex (papers, controls excluded: " + str(len(P)) + ")", "",
              "| measure | count |", "|---|---|",
              f"| papers with an OpenAlex candidate | {sum(1 for p in P if openalex_candidates(p))} |",
              f"| same paper confirmed (any version) | {sum(1 for v in pr.values() if v.get('same_paper') == 'confirmed')} |",
              f"| arXiv link recovered (confirmed) | {sum(1 for v in pr.values() if v.get('arxiv') == 'confirmed')} |",
              f"| arXiv HTML our parser can read | {sum(1 for v in pr.values() if v.get('html') == 'yes')} |",
              f"| arXiv candidate rejected (title similar, authors differ) | {sum(1 for v in pr.values() if v.get('arxiv') == 'rejected')} |",
              f"| no arXiv location listed for the confirmed paper | {sum(1 for v in pr.values() if v.get('arxiv') == 'none_listed')} |",
              f"| unresolved | {sum(1 for v in pr.values() if v.get('same_paper') == 'unresolved')} |", "",
              "arXiv recoveries by source: " + "; ".join(f"{k} {n}" for k, n in via.items()), "",
              "arXiv recoveries by sample stratum: " + "; ".join(f"{k} {n}" for k, n in sorted(by_band.items())), ""]
    B = [b for b in s["benchmarks"] if not b["control"]]
    br = {b["id"]: rev["benchmarks"].get(b["id"], {}) for b in B}

    def cls(statuses):
        st = statuses or ["unresolved"]
        return "confirmed" if "confirmed" in st else "exists_unconfirmed" if "exists_unconfirmed" in st else "rejected only" if "rejected" in st else "nothing found"
    lines += ["## Locations (benchmarks, controls excluded: " + str(len(B)) + ")", "",
              "Each benchmark counted once, by its best outcome on that side.", "",
              "| outcome | baseline (introducing paper's own text) | Tavily |", "|---|---|---|"]
    for label in ("confirmed", "exists_unconfirmed", "rejected only", "nothing found"):
        lines.append(f"| {label} | {sum(1 for v in br.values() if cls(v.get('baseline')) == label)} | {sum(1 for v in br.values() if cls(v.get('tavily')) == label)} |")
    lines += [f"| benchmarks with at least one rejected candidate (false positive) | {sum(1 for v in br.values() if 'rejected' in (v.get('baseline') or []))} | {sum(1 for v in br.values() if 'rejected' in (v.get('tavily') or []))} |", "",
              f"Confirmed by either: {sum(1 for v in br.values() if 'confirmed' in (v.get('baseline') or []) + (v.get('tavily') or []))}; "
              f"by Tavily and not the baseline: {sum(1 for v in br.values() if 'confirmed' in (v.get('tavily') or []) and 'confirmed' not in (v.get('baseline') or []))}; "
              f"by the baseline and not Tavily: {sum(1 for v in br.values() if 'confirmed' in (v.get('baseline') or []) and 'confirmed' not in (v.get('tavily') or []))}", ""]
    use = collections.Counter((r["service"], r["event"]) for r in ledger)
    cost = sum(float((r.get("usage") or {}).get("cost_usd") or 0) for r in ledger if r["service"] == "openalex" and r["event"] == "ok")
    credits = sum(float((r.get("usage") or {}).get("credits") or 0) for r in ledger if r["service"].startswith("tavily") and r["event"] == "ok")
    lines += ["## Requests", "", "| service | sent | ok | errors | no response | cache hits | reported usage |", "|---|---|---|---|---|---|---|"]
    for sv in ("openalex", "tavily_search", "tavily_extract"):
        rep = f"${cost:.4f} (meta.cost_usd)" if sv == "openalex" else f"{credits:g} credits (usage.credits, both Tavily services)" if sv == "tavily_search" else ""
        lines.append(f"| {sv} | {use[(sv, 'sent')]} | {use[(sv, 'ok')]} | {use[(sv, 'http_error')]} | {use[(sv, 'no_response')]} | {use[(sv, 'cache_hit')]} | {rep} |")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["sample", "baseline", "openalex", "openalex-arxiv", "arxiv-authors", "tavily", "extract", "checks", "report"])
    ap.add_argument("--limit", type=int, default=10 ** 6)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("urls", nargs="*")
    a = ap.parse_args()
    if a.command == "sample":
        cmd_sample(a.force)
    elif a.command == "baseline":
        cmd_baseline()
    elif a.command == "openalex":
        cmd_openalex(a.limit)
    elif a.command == "openalex-arxiv":
        cmd_openalex_arxiv()
    elif a.command == "arxiv-authors":
        cmd_arxiv_authors()
    elif a.command == "tavily":
        cmd_tavily(a.limit)
    elif a.command == "extract":
        cmd_extract(a.urls)
    elif a.command == "checks":
        cmd_checks()
    else:
        cmd_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
