"""Paper lists for every edition in corpus.EDITIONS, whatever site publishes them.

Seven venues run the same "virtual site" software as ICML and publish one JSON
feed per edition; the rest publish elsewhere. Each adapter below turns its
source into a snapshot shaped exactly like a virtual-site feed, so
`icml.normalize` reads every venue the same way and nothing downstream knows
where a list came from.

    python3 -m icml.sources collect --all          # every edition without a snapshot
    python3 -m icml.sources collect --venue aaai --year 2026 [--force]
    python3 -m icml.sources abstracts --venue iccv --year 2023
    python3 -m icml.sources status                 # editions, papers, abstracts, arXiv match

Sources (verified 2026-10-02):

    virtual   https://<site>/static/virtual/data/<venue>-<year>-orals-posters.json
              ICML 2024-26, ICLR 2024-26, NeurIPS 2023-25, CVPR 2024-25,
              ICCV 2025, ECCV 2024 and 2026 (2026 has no abstracts in the feed)
    cvf       openaccess.thecvf.com/<CONF><year>?day=all   ICCV 2023, CVPR 2026
              (the CVPR 2026 virtual feed is a 200-row placeholder)
    aaai      ojs.aaai.org issue archive. Kept: "AAAI Technical Track ..." and
              "AAAI Special Track ..." sections. Left out: student abstracts,
              demos, senior-member and new-faculty talks, the journal track,
              IAAI and EAAI.
    anthology aclanthology.org anthology+abstracts.bib.gz, one download for
              every volume. ACL = <year>.acl-long + <year>.acl-short; EMNLP =
              <year>.emnlp-main. Findings, demos and industry tracks are out.
    pmlr      CoRL volume bibliography: v229 (2023), v270 (2024), v305 (2025)

Snapshots: data/raw/virtual_feed_<venue>_<year>_<date>.json (ICML keeps its
historical virtual_feed_<year>_<date>.json). Abstracts that need one page per
paper go to data/raw/abstracts_<venue>_<year>.jsonl, which normalize already
reads. A failed page is not written, so re-running retries it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import re
import time
import unicodedata
from pathlib import Path

from .common import RAW, dump_json, ensure_dirs, fetch, load_json, read_jsonl
from .corpus import EDITIONS, VENUES

VIRTUAL_SITE = {
    "icml": "icml.cc", "iclr": "iclr.cc", "neurips": "neurips.cc",
    "cvpr": "cvpr.thecvf.com", "iccv": "iccv.thecvf.com", "eccv": "eccv.ecva.net",
}

# (venue, year) -> (adapter, argument). Anything absent uses its virtual feed.
SPECIAL = {
    ("iccv", 2023): ("cvf", "ICCV2023"),
    ("cvpr", 2026): ("cvf", "CVPR2026"),
    ("aaai", 2024): ("aaai", "AAAI-24"),
    ("aaai", 2025): ("aaai", "AAAI-25"),
    ("aaai", 2026): ("aaai", "AAAI-26"),
    ("acl", 2024): ("anthology", ["2024.acl-long", "2024.acl-short"]),
    ("acl", 2025): ("anthology", ["2025.acl-long", "2025.acl-short"]),
    ("acl", 2026): ("anthology", ["2026.acl-long", "2026.acl-short"]),
    ("emnlp", 2023): ("anthology", ["2023.emnlp-main"]),
    ("emnlp", 2024): ("anthology", ["2024.emnlp-main"]),
    ("emnlp", 2025): ("anthology", ["2025.emnlp-main"]),
    ("corl", 2023): ("pmlr", "v229"),
    ("corl", 2024): ("pmlr", "v270"),
    ("corl", 2025): ("pmlr", "v305"),
}

ANTHOLOGY_DUMP = RAW / "acl" / "anthology+abstracts.bib.gz"
ANTHOLOGY_URL = "https://aclanthology.org/anthology+abstracts.bib.gz"
DELAY = 0.25          # seconds between per-paper page requests, per process


def key_of(display: str) -> str:
    return next(k for k, v in VENUES.items() if v == display)


def snapshot_glob(venue: str, year: int) -> str:
    return f"virtual_feed_{year}_*.json" if venue == "icml" \
        else f"virtual_feed_{venue}_{year}_*.json"


def latest_snapshot(venue: str, year: int) -> Path | None:
    snaps = sorted(RAW.glob(snapshot_glob(venue, year)))
    return snaps[-1] if snaps else None


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def stable_id(s: str) -> int:
    """A positive int that does not move between snapshots: event_id must be an
    int everywhere downstream, and these sources have no numeric id of their
    own. Collisions are checked when the snapshot is written."""
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16)


def record(rid: int, title: str, authors: list[str], **kw) -> dict:
    """One feed row, in the virtual-site shape icml.normalize reads."""
    return {"id": rid, "name": title,
            "authors": [{"fullname": a, "institution": None} for a in authors],
            "abstract": kw.get("abstract"), "eventtype": kw.get("eventtype", "Poster"),
            "decision": kw.get("decision"), "topic": kw.get("topic"),
            "sourceurl": kw.get("sourceurl"), "paper_url": kw.get("paper_url"),
            "virtualsite_url": None, "visible": True}


# ---------------------------------------------------------------- adapters

def from_virtual(venue: str, year: int) -> tuple[list[dict], str]:
    url = f"https://{VIRTUAL_SITE[venue]}/static/virtual/data/{venue}-{year}-orals-posters.json"
    return json.loads(fetch(url, timeout=240))["results"], url


def from_cvf(conf: str) -> tuple[list[dict], str]:
    url = f"https://openaccess.thecvf.com/{conf}?day=all"
    page = fetch(url, timeout=240).decode("utf-8", "replace")
    rows = []
    for block in page.split('<dt class="ptitle">')[1:]:
        m = re.search(r'<a href="([^"]+)">(.*?)</a>', block, re.S)
        if not m:
            continue
        head = block.split("<dt", 1)[0]
        authors = re.findall(r'name="query_author" value="([^"]+)"', head)
        link = "https://openaccess.thecvf.com" + m.group(1)
        rows.append(record(stable_id(m.group(1)), _text(m.group(2)),
                           [html.unescape(a) for a in authors],
                           paper_url=link, sourceurl=f"cvf:{conf}"))
    return rows, url


def from_aaai(label: str) -> tuple[list[dict], str]:
    """Every issue whose title carries `label` (e.g. "AAAI-26"), main and
    special technical-track sections only."""
    base = "https://ojs.aaai.org/index.php/AAAI/issue"
    issues: dict[str, str] = {}
    for page in range(1, 10):
        t = fetch(f"{base}/archive" + ("" if page == 1 else f"/{page}"),
                  timeout=120).decode("utf-8", "replace")
        found = [(i, _text(n)) for i, n in re.findall(r'issue/view/(\d+)"[^>]*>\s*([^<]+?)\s*<', t)]
        for i, n in found:
            if n:
                issues.setdefault(i, n)
        if not found:
            break
        years = [int(y) for y in re.findall(r"AAAI-(\d\d)\b", " ".join(n for _, n in found))]
        if years and max(years) < int(label[-2:]):
            break                                   # archive is newest-first
    mine = [i for i, n in issues.items() if re.search(rf"(^|\W){label}(\W|$)", n)]
    rows = []
    for iid in mine:
        t = fetch(f"{base}/view/{iid}", timeout=120).decode("utf-8", "replace")
        for sec in t.split('<div class="section">')[1:]:
            h = re.search(r"<h2>\s*(.*?)\s*</h2>", sec, re.S)
            name = _text(h.group(1)) if h else ""
            if not re.match(r"AAAI (Technical|Special) Track", name):
                continue
            for art in sec.split('<div class="obj_article_summary">')[1:]:
                a = re.search(r'article/view/(\d+)"[^>]*>(.*?)</a>', art, re.S)
                au = re.search(r'<div class="authors">(.*?)</div>', art, re.S)
                if not a:
                    continue
                authors = [x.strip() for x in _text(au.group(1)).split(",")] if au else []
                rows.append(record(int(a.group(1)), _text(a.group(2)), [x for x in authors if x],
                                   topic=name, decision=name, sourceurl=f"aaai:issue/{iid}",
                                   paper_url=f"https://ojs.aaai.org/index.php/AAAI/article/view/{a.group(1)}"))
        time.sleep(DELAY)
    return rows, f"{base}/archive ({label}: {len(mine)} issues)"


_LATEX = [(r"``|''", '"'), (r"\\&", "&"), (r"\\%", "%"), (r"\\_", "_"), (r"---?", "-"), (r"[{}]", "")]
_ACCENTS = {"'": "́", "`": "̀", "^": "̂", '"': "̈", "~": "̃",
            "=": "̄", ".": "̇", "u": "̆", "v": "̌", "H": "̋",
            "c": "̧"}


def delatex(s: str) -> str:
    """BibTeX value -> plain text: accent commands composed, case braces dropped."""
    s = re.sub(r"\{?\\([`'^\"~=.uvHc])\{?(\w)\}?\}?",
               lambda m: unicodedata.normalize("NFC", m.group(2) + _ACCENTS[m.group(1)]), s)
    for pat, rep in _LATEX:
        s = re.sub(pat, rep, s)
    return re.sub(r"\s+", " ", s).strip()


def parse_bibtex(text: str) -> list[dict]:
    """Entries of a BibTeX file as {field: raw value}. Handles "..." and {...}
    values with nested braces — enough for the Anthology and PMLR exports."""
    out = []
    for chunk in re.split(r"\n@", "\n" + text)[1:]:
        m = re.match(r"(\w+)\s*\{\s*([^,\s]+)\s*,", chunk)
        if not m:
            continue
        entry = {"_type": m.group(1).lower(), "_key": m.group(2)}
        i = m.end()
        while True:
            f = re.compile(r"\s*(\w+)\s*=\s*").match(chunk, i)
            if not f:
                break
            name, i = f.group(1).lower(), f.end()
            if i < len(chunk) and chunk[i] in "{\"":
                close, depth, j = ("}" if chunk[i] == "{" else '"'), 0, i + 1
                while j < len(chunk):
                    c = chunk[j]
                    if c == "{":
                        depth += 1
                    elif c == "}" and depth:
                        depth -= 1
                    elif c == close and depth == 0:
                        break
                    j += 1
                entry[name], i = chunk[i + 1:j], j + 1
            else:
                v = re.compile(r"[^,}\s]+").match(chunk, i)
                entry[name], i = (v.group(0), v.end()) if v else ("", i)
            c = re.compile(r"\s*,?").match(chunk, i)
            i = c.end()
        out.append(entry)
    return out


def bib_authors(raw: str) -> list[str]:
    names = []
    for a in re.split(r"\s+and\s+", delatex(raw or "")):
        a = a.strip()
        if "," in a:
            last, first = (x.strip() for x in a.split(",", 1))
            a = f"{first} {last}".strip()
        if a:
            names.append(a)
    return names


def from_anthology(volumes: list[str]) -> tuple[list[dict], str]:
    if not ANTHOLOGY_DUMP.exists():
        ANTHOLOGY_DUMP.parent.mkdir(parents=True, exist_ok=True)
        ANTHOLOGY_DUMP.write_bytes(fetch(ANTHOLOGY_URL, timeout=600))
    raw = ANTHOLOGY_DUMP.read_bytes()
    text = (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode("utf-8", "replace")
    rows = []
    for e in parse_bibtex(text):
        m = re.search(r"aclanthology\.org/(([0-9]{4}\.[a-z-]+)\.(\d+))", e.get("url", ""))
        if not m or m.group(2) not in volumes or m.group(3) == "0" or e["_type"] != "inproceedings":
            continue
        rows.append(record(volumes.index(m.group(2)) * 100000 + 100000 + int(m.group(3)),
                           delatex(e.get("title", "")), bib_authors(e.get("author")),
                           abstract=delatex(e.get("abstract", "")) or None,
                           topic=m.group(2), sourceurl=f"anthology:{m.group(2)}",
                           paper_url=f"https://aclanthology.org/{m.group(1)}"))
    return rows, f"{ANTHOLOGY_URL} ({', '.join(volumes)})"


def from_pmlr(volume: str) -> tuple[list[dict], str]:
    url = f"https://proceedings.mlr.press/{volume}/assets/bib/bibliography.bib"
    text = fetch(url, timeout=240).decode("utf-8", "replace")
    rows = []
    for e in parse_bibtex(text):
        if e["_type"] != "inproceedings":
            continue
        rows.append(record(stable_id(e["_key"]), delatex(e.get("title", "")),
                           bib_authors(e.get("author")),
                           abstract=delatex(e.get("abstract", "")) or None,
                           sourceurl=f"pmlr:{volume}",
                           paper_url=e.get("url") or f"https://proceedings.mlr.press/{volume}/"))
    return rows, url


# ---------------------------------------------------------------- commands

def collect(venue: str, year: int, force: bool) -> None:
    have = latest_snapshot(venue, year)
    if have and not force:
        print(f"[{venue} {year}] snapshot present: {have.name}")
        return
    kind, arg = SPECIAL.get((venue, year), ("virtual", None))
    rows, src = {
        "virtual": lambda: from_virtual(venue, year),
        "cvf": lambda: from_cvf(arg), "aaai": lambda: from_aaai(arg),
        "anthology": lambda: from_anthology(arg), "pmlr": lambda: from_pmlr(arg),
    }[kind]()
    if kind != "virtual":
        # PMLR bibliographies repeat some entries verbatim; one paper, one row.
        # The same id on two different titles is a real collision.
        by_id: dict[int, dict] = {}
        for r in rows:
            prev = by_id.setdefault(r["id"], r)
            if prev is not r and prev["name"] != r["name"]:
                raise RuntimeError(f"id collision in {kind} adapter: {prev['name']!r} / {r['name']!r}")
        if len(by_id) != len(rows):
            print(f"[{venue} {year}] dropped {len(rows) - len(by_id)} repeated entries")
        rows = list(by_id.values())
    if not rows:
        print(f"[{venue} {year}] source returned no rows — nothing written")
        return
    stamp = dt.date.today().isoformat()
    name = f"virtual_feed_{year}_{stamp}.json" if venue == "icml" \
        else f"virtual_feed_{venue}_{year}_{stamp}.json"
    dump_json(RAW / name, {"source": src, "adapter": kind,
                           "fetched_at": dt.datetime.now().astimezone().isoformat(),
                           "count": len(rows), "results": rows}, indent=None)
    with_abs = sum(1 for r in rows if (r.get("abstract") or "").strip())
    print(f"[{venue} {year}] {kind}: {len(rows)} rows, abstracts {with_abs} -> {name}")


_ABS_PATTERNS = {
    "virtual": re.compile(r'<div class="abstract-content">(.*?)</div>\s*</div>', re.S),
    "cvf": re.compile(r'<div id="abstract">(.*?)</div>', re.S),
    "aaai": re.compile(r'<section class="item abstract">\s*<h2 class="label">[^<]*</h2>(.*?)</section>', re.S),
}


def abstracts(venue: str, year: int, limit: int = 0) -> None:
    """Fill abstracts the snapshot lacks, one page per paper."""
    snap = latest_snapshot(venue, year)
    if snap is None:
        raise SystemExit(f"no snapshot for {venue} {year} — run collect first")
    kind = SPECIAL.get((venue, year), ("virtual", None))[0]
    if kind not in _ABS_PATTERNS:
        print(f"[{venue} {year}] {kind} snapshots carry their abstracts — nothing to fetch")
        return
    out = RAW / f"abstracts_{venue}_{year}.jsonl"
    done = {r["id"] for r in read_jsonl(out)}
    todo = [r for r in load_json(snap)["results"]
            if not (r.get("abstract") or "").strip() and r["id"] not in done]
    if limit:
        todo = todo[:limit]
    print(f"[{venue} {year}] {len(done)} done, {len(todo)} to fetch", flush=True)
    ok = fail = 0
    with out.open("a", encoding="utf-8") as fh:
        for i, r in enumerate(todo, 1):
            url = (f"https://{VIRTUAL_SITE[venue]}/virtual/{year}/poster/{r['id']}"
                   if kind == "virtual" else r["paper_url"])
            try:
                m = _ABS_PATTERNS[kind].search(fetch(url, timeout=60).decode("utf-8", "replace"))
                text = _text(m.group(1)) if m else ""
                if not text:
                    raise ValueError("no abstract on page")
                fh.write(json.dumps({"id": r["id"], "abstract": text}, ensure_ascii=False) + "\n")
                ok += 1
            except Exception as exc:  # noqa: BLE001 — a failure is retried next run
                fail += 1
                if fail <= 10:
                    print(f"  fail {r['id']}: {type(exc).__name__}: {exc}", flush=True)
            if i % 200 == 0:
                fh.flush()
                print(f"  {i}/{len(todo)} ok={ok} fail={fail}", flush=True)
            time.sleep(DELAY)
    print(f"[{venue} {year}] done: ok={ok} fail={fail} -> {out.name}")


def status() -> None:
    """One line per edition: what is collected, and how far it got."""
    from .corpus import Corpus
    print(f"{'edition':14s} {'source':9s} {'rows':>6s} {'papers':>6s} {'abstr':>6s} {'arXiv':>6s}")
    for display, year in EDITIONS:
        venue = key_of(display)
        kind = SPECIAL.get((venue, year), ("virtual", None))[0]
        snap = latest_snapshot(venue, year)
        rows = load_json(snap)["count"] if snap and "count" in load_json(snap) else (
            len(load_json(snap)["results"]) if snap else 0)
        c = Corpus(display, year)
        papers = list(read_jsonl(c.papers)) if c.papers.exists() else []
        ab = sum(1 for p in papers if p.get("abstract"))
        res = RAW / "arxiv" / ("resolved.jsonl" if c.is_focus else f"resolved_{c.key}.jsonl")
        m = [r for r in read_jsonl(res)] if res.exists() else []
        hit = sum(1 for r in m if r.get("arxiv_id"))
        pct = lambda a, b: f"{100 * a / b:5.1f}%" if b else "     -"
        print(f"{display + ' ' + str(year):14s} {kind:9s} {rows:6d} {len(papers):6d} "
              f"{pct(ab, len(papers))} {pct(hit, len(m))}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect paper lists for every edition in scope.")
    ap.add_argument("command", choices=["collect", "abstracts", "status"])
    ap.add_argument("--venue", choices=sorted(VENUES))
    ap.add_argument("--year", type=int)
    ap.add_argument("--all", action="store_true", help="every edition in corpus.EDITIONS")
    ap.add_argument("--force", action="store_true", help="re-fetch even if a snapshot exists")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    ensure_dirs()
    if args.command == "status":
        status()
        return 0
    targets = [(key_of(v), y) for v, y in EDITIONS] if args.all else [(args.venue, args.year)]
    if not args.all and not (args.venue and args.year):
        ap.error("give --venue and --year, or --all")
    for venue, year in targets:
        try:
            if args.command == "collect":
                collect(venue, year, args.force)
            else:
                abstracts(venue, year, args.limit)
        except Exception as exc:  # noqa: BLE001 — one bad source must not stop the rest
            print(f"[{venue} {year}] FAILED: {type(exc).__name__}: {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
