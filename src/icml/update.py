"""Incremental processing: one working database of per-paper stage status,
and `run`, which brings every edition up to date and publishes a revision.

    python3 -m icml.update status                 # the funnel per edition, what is stale, what failed
    python3 -m icml.update run [--publish] [--if-due 7] [--edition iclr-2025 ...] [--dry-run]

The working database (`data/work/pipeline.sqlite`) is operational state and
never enters a published revision: per (edition, paper) the stage reached,
the version of the code that produced it, failures with their retry state,
the claims waiting for review, and every run with its outcome. The research
content stays in the pipeline's own files (normalized papers, arXiv
matches, parsed full text, mention outputs, the reviewed catalogue); a
revision is built from those by `bench2agent benchmarks`.

Re-run scope, by what changed:
- a new edition or new papers in one: match -> fetch+parse -> mentions -> claims;
- a paper's parse failed transiently: fetched again next run (a permanent
  failure, "no-html" or a thin page, is kept and reported, never retried
  blindly);
- the parser changed (its source hash): every edition's archived HTML is
  re-parsed (`--reparse`), then everything downstream;
- the mention rules or the registry changed (their hash): mentions are
  re-extracted for every parsed paper, then claims;
- a claim's evidence sentence changed, or a claim is new: it goes to the
  review queue; a reviewed claim whose sentence changed is stale and counts
  nowhere until re-reviewed (`bench_mentions.introduced`).

One run at a time (`data/work/update.lock`); `--if-due N` runs only when the
last successful run is older than N days, so an OS timer can call it daily
and a missed week is caught up once, at the next start.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from .common import ROOT, read_jsonl
from .corpus import EDITIONS, Corpus

STAGES = ("normalized", "matched", "parsed", "mentions")


def work_dir(root: Path = ROOT) -> Path:
    return root / "data" / "work"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, outcome TEXT,
    kind TEXT NOT NULL, versions TEXT NOT NULL, summary TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS edition_stages (
    edition TEXT NOT NULL, stage TEXT NOT NULL, version TEXT, input_hash TEXT, finished_at TEXT, run_id INTEGER,
    PRIMARY KEY (edition, stage));
CREATE TABLE IF NOT EXISTS paper_stages (
    edition TEXT NOT NULL, event_id TEXT NOT NULL, arxiv_base TEXT, stage TEXT NOT NULL, status TEXT NOT NULL,
    error TEXT, transient INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
    source_version TEXT,
    PRIMARY KEY (edition, event_id));
CREATE TABLE IF NOT EXISTS claim_queue (
    claim_id TEXT PRIMARY KEY, edition TEXT NOT NULL, reason TEXT NOT NULL, evidence_sha TEXT NOT NULL,
    queued_at TEXT NOT NULL, run_id INTEGER);
"""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    sha = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()[:16]


def versions(root: Path = ROOT) -> dict:
    """What produced each stage: the code and inputs whose change makes a
    stage stale. Short hashes of the sources, so a run can say which."""
    src = root / "src" / "icml"
    return {"normalizer": file_hash(src / "normalize.py"),
            "matcher": file_hash(src / "arxiv_match.py"),
            "parser": file_hash(src / "html_extract.py"),
            # the mention vocabulary comes from the registry AND the reviewed catalogue's
            # entities (ids, names, aliases, homonym grouping) — not from the catalogue
            # file, which every run regenerates downstream of the mentions themselves
            "rules": hashlib.sha256("|".join(filter(None, [
                file_hash(src / "bench_mentions.py"), file_hash(src / "taxonomy.py"),
                file_hash(root / "config" / "benchmarks.json"), vocabulary_signature(root)])).encode()).hexdigest()[:16]}


def vocabulary_signature(root: Path = ROOT) -> str | None:
    """What of the reviewed catalogue reaches the mention vocabulary: each
    entity's id, name, aliases and whether it shares its name. A new claim
    that is not yet confirmed changes nothing here; a confirmed new entity
    or a split does, and then mentions are stale by design."""
    path = root / "config" / "benchmarks_introduced.json"
    if not path.exists():
        return None
    entries = json.loads(path.read_text(encoding="utf-8")).get("benchmarks", [])
    sig = sorted((e["id"], e.get("name"), tuple(e.get("aliases") or []), bool(e.get("homonym_of")), e.get("registered")) for e in entries)
    return hashlib.sha256(json.dumps(sig).encode()).hexdigest()[:16]


def edition_paths(disp: str, year: int, root: Path = ROOT) -> dict:
    """The pipeline's files for one edition, under `root`, named as
    `icml.corpus.Corpus` names them (the first corpus kept its old names)."""
    c = Corpus(disp, year)
    key, venue = c.key, disp.lower()
    stem = "papers" if c.is_focus else (f"papers_{year}" if venue == "icml" else f"papers_{venue}_{year}")
    return {"key": key, "venue": venue, "year": year, "papers": root / "data/processed" / f"{stem}.jsonl",
            "resolved": root / "data/raw/arxiv" / ("resolved.jsonl" if c.is_focus else f"resolved_{key}.jsonl"),
            "fulltext": root / "data/interim/html" / f"fulltext_{key}.jsonl",
            "mentions": root / "data/interim/mentions" / f"mentions_{key}.jsonl", "archive": root / "data/raw/html"}


class Work:
    """The working database. Every write goes through one connection."""

    def __init__(self, root: Path = ROOT):
        self.root = root
        path = work_dir(root) / "pipeline.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        # a working database from before a column existed gains it here
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(paper_stages)")}
        if "source_version" not in cols:
            self.con.execute("ALTER TABLE paper_stages ADD COLUMN source_version TEXT")
            self.con.commit()

    def close(self) -> None:
        self.con.close()

    def get(self, key: str, default=None):
        row = self.con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        self.con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))
        self.con.commit()

    def stage_version(self, edition: str, stage: str) -> tuple[str | None, str | None]:
        row = self.con.execute("SELECT version, input_hash FROM edition_stages WHERE edition = ? AND stage = ?",
                               (edition, stage)).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def mark_stage(self, edition: str, stage: str, version: str | None, input_hash: str | None, run_id: int | None) -> None:
        self.con.execute("INSERT OR REPLACE INTO edition_stages VALUES (?,?,?,?,?,?)",
                         (edition, stage, version, input_hash, now(), run_id))
        self.con.commit()

    def record_papers(self, edition: str, rows: list[tuple]) -> None:
        """rows: (event_id, arxiv_base, stage, status, error, transient)"""
        ts = now()
        self.con.executemany(
            "INSERT INTO paper_stages (edition, event_id, arxiv_base, stage, status, error, transient, attempts, updated_at) "
            "VALUES (?,?,?,?,?,?,?,1,?) ON CONFLICT(edition, event_id) DO UPDATE SET arxiv_base = excluded.arxiv_base, "
            "stage = excluded.stage, status = excluded.status, error = excluded.error, transient = excluded.transient, "
            "attempts = CASE WHEN excluded.status = 'failed' THEN paper_stages.attempts + 1 ELSE paper_stages.attempts END, "
            "updated_at = excluded.updated_at",
            [(edition, str(e), b, st, status, err, int(bool(tr)), ts) for e, b, st, status, err, tr in rows])
        self.con.commit()

    def record_transients(self, edition: str, failures: list[tuple[str, str, str]]) -> None:
        """(event_id, arxiv_base, error): a fetch the parser did not record;
        retried at the next run, and counted for the retry schedule."""
        ts = now()
        self.con.executemany(
            "INSERT INTO paper_stages (edition, event_id, arxiv_base, stage, status, error, transient, attempts, updated_at) "
            "VALUES (?,?,?,?,'transient',?,1,1,?) ON CONFLICT(edition, event_id) DO UPDATE SET status = 'transient', "
            "error = excluded.error, transient = 1, attempts = paper_stages.attempts + 1, updated_at = excluded.updated_at",
            [(edition, str(e), b, "parsed", err, ts) for e, b, err in failures])
        self.con.commit()

    def transients(self, edition: str) -> list[dict]:
        return [dict(r) for r in self.con.execute(
            "SELECT event_id, arxiv_base, error, attempts, updated_at FROM paper_stages WHERE edition = ? AND status = 'transient'", (edition,))]

    def pending_retries(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM paper_stages WHERE status IN ('transient', 'pending')").fetchone()[0]

    def source_versions(self, edition: str) -> dict[str, str]:
        return {r[0]: r[1] for r in self.con.execute(
            "SELECT arxiv_base, source_version FROM paper_stages WHERE edition = ? AND source_version IS NOT NULL", (edition,))}

    def set_source_versions(self, edition: str, versions: dict[str, str]) -> None:
        self.con.executemany("UPDATE paper_stages SET source_version = ? WHERE edition = ? AND arxiv_base = ?",
                             [(v, edition, b) for b, v in versions.items()])
        self.con.commit()

    def queue_claims(self, items: list[tuple[str, str, str, str]], run_id: int | None) -> None:
        self.con.executemany("INSERT OR REPLACE INTO claim_queue VALUES (?,?,?,?,?,?)",
                             [(cid, ed, reason, sha, now(), run_id) for cid, ed, reason, sha in items])
        self.con.commit()

    def dequeue_claims(self, claim_ids: list[str]) -> None:
        self.con.executemany("DELETE FROM claim_queue WHERE claim_id = ?", [(c,) for c in claim_ids])
        self.con.commit()

    def start_run(self, kind: str) -> int:
        cur = self.con.execute("INSERT INTO runs (started_at, kind, versions) VALUES (?,?,?)", (now(), kind, json.dumps(versions(self.root))))
        self.con.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, outcome: str, summary: dict | None = None, error: str | None = None) -> None:
        self.con.execute("UPDATE runs SET finished_at = ?, outcome = ?, summary = ?, error = ? WHERE run_id = ?",
                         (now(), outcome, json.dumps(summary or {}), error, run_id))
        self.con.commit()
        if outcome == "ok":
            self.set("last_success", now())
        elif outcome == "adopted":
            pass

    def last_success(self) -> str | None:
        return self.get("last_success")


def index_dates(root: Path = ROOT) -> dict[str, str]:
    """arxiv_base -> newest datestamp in the OAI index: the source version of
    a paper's text. A record whose datestamp moved past the one a parse was
    made on is a new version, to be fetched and parsed again."""
    path = root / "data/raw/arxiv/oai_index.jsonl"
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for r in read_jsonl(path):
        d = r.get("date") or ""
        if d >= out.get(r["arxiv_base"], ""):
            out[r["arxiv_base"]] = d
    return out


def open_editions(today: dt.date | None = None) -> set[str]:
    """Editions whose lists can still change: this year's and last year's."""
    y = (today or dt.date.today()).year
    return {Corpus(d, yr).key for d, yr in EDITIONS if yr >= y - 1}


# ---------------------------------------------------------------- the funnel from the files

def edition_funnel(paths: dict) -> dict:
    """Per edition, what each file holds: papers, arXiv-matched, parsed (ok /
    failed permanently / failed transiently), with mentions. Read from the
    files themselves, so the status never says more than the data holds."""
    papers = {str(r["event_id"]): r for r in read_jsonl(paths["papers"])} if paths["papers"].exists() else {}
    matched: dict[str, str] = {}
    collisions = 0
    if paths["resolved"].exists():
        for r in read_jsonl(paths["resolved"]):
            if r.get("arxiv_base"):
                matched.setdefault(r["arxiv_base"], str(r["event_id"]))
            elif r.get("match") == "collision":
                collisions += 1
    parsed: dict[str, dict] = {}
    if paths["fulltext"].exists():
        for r in read_jsonl(paths["fulltext"]):
            parsed[r["arxiv_base"]] = {"ok": bool(r.get("ok")), "error": r.get("error"), "transient": bool(r.get("transient"))}
    mentioned: set[str] = set()
    if paths["mentions"].exists():
        for r in read_jsonl(paths["mentions"]):
            mentioned.add(r["arxiv_base"])
    ok = {b for b, p in parsed.items() if p["ok"]}
    return {"papers": len(papers), "matched": len(matched), "collisions": collisions,
            "parsed_ok": len(ok & set(matched)), "parsed_failed": sum(1 for b, p in parsed.items() if not p["ok"] and not p["transient"]),
            "parsed_transient": sum(1 for b, p in parsed.items() if not p["ok"] and p["transient"]),
            "unparsed": sorted(set(matched) - set(parsed)), "mentions": len(mentioned & ok),
            "mentions_missing": sorted(ok - mentioned),
            "_papers": papers, "_matched": matched, "_parsed": parsed}


# ---------------------------------------------------------------- the pipeline

class Pipeline:
    """The stages as overridable methods, so tests can replace the ones that
    need the network or the parser environment."""

    def __init__(self, work: Work, *, python: str | None = None, parser_python: str | None = None, log=print):
        self.work = work
        self.python = python or sys.executable
        self.parser_python = parser_python or os.environ.get("BENCH2AGENT_PARSER_PYTHON") or self._parser_python()
        self.log = log

    @staticmethod
    def _parser_python() -> str | None:
        try:
            import lxml  # noqa: F401
            return sys.executable
        except ImportError:
            return None

    def _run(self, cmd: list[str]) -> str:
        root = self.work.root
        out = subprocess.run(cmd, cwd=root, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(root / "src")})
        if out.returncode:
            raise RuntimeError(f"{' '.join(cmd[:4])}: {out.stderr.strip()[-400:]}")
        return out.stdout

    def harvest(self, since: str, until: str) -> str:
        """Refresh the arXiv index for records changed in [since, until]."""
        return self._run([self.python, "-m", "icml.arxiv_harvest", "--since", since, "--until", until]).strip()[-300:]

    def collect(self, paths: dict) -> None:
        """Re-fetch an edition's paper list from its source (feeds change while a conference is current)."""
        self._run([self.python, "-m", "icml.sources", "collect", "--venue", paths["venue"], "--year", str(paths["year"]), "--force"])

    def index_dates(self) -> dict[str, str]:
        return index_dates(self.work.root)

    def normalize(self, paths: dict) -> None:
        self._run([self.python, "-m", "icml.normalize", "--venue", paths["venue"], "--year", str(paths["year"]), "--allow-missing-abstracts"])

    def match(self, paths: dict) -> None:
        self._run([self.python, "-m", "icml.arxiv_match", "--venue", paths["venue"], "--year", str(paths["year"])])

    def parse(self, paths: dict, *, reparse: bool, limit: int = 0, drop: set[str] = frozenset()) -> tuple[str, list[tuple[str, str]]]:
        """html_extract appends rows for every resolved base absent from the
        output. Rows to fetch again are dropped from the file first: every
        row under `reparse`, transiently failed rows, and `drop` (papers with
        a new source version or a failure to re-judge); their archived HTML
        is removed too, so they are fetched, not re-read. Returns html_extract's
        final summary line and the transient failures it reported
        (arxiv_base, error), which it never records as rows."""
        if not self.parser_python:
            raise RuntimeError("no parser environment: lxml is not importable and BENCH2AGENT_PARSER_PYTHON is not set")
        if not Path(self.parser_python).exists():
            raise RuntimeError(f"parser environment not found: {self.parser_python} (BENCH2AGENT_PARSER_PYTHON)")
        ft = paths["fulltext"]
        if ft.exists():
            rows = [] if reparse else [r for r in read_jsonl(ft)
                                       if not (not r.get("ok") and r.get("transient")) and r["arxiv_base"] not in drop]
            tmp = ft.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmp.replace(ft)
        for base in drop:
            cached = paths["archive"] / f"{base}.html.gz"
            if cached.exists():
                cached.unlink()
        cmd = [self.parser_python, "-m", "icml.html_extract", "--resolved", str(paths["resolved"]), "--out", str(ft),
               "--archive", str(paths["archive"]), "--reparse"]
        if limit:
            cmd += ["--limit", str(limit)]
        out = self._run(cmd)
        lines = out.splitlines()
        summary = next((l.strip() for l in reversed(lines) if l.startswith("done")), out.strip()[-200:])
        transient = [(l.split()[1], " ".join(l.split()[2:])) for l in lines if l.startswith("transient ") and len(l.split()) >= 2]
        return summary, transient

    def mentions(self, paths: dict) -> None:
        from . import bench_mentions as bm
        fold_to, casings, _ = bm.vocabulary()
        bm.run(paths["key"], bm.Finder(fold_to, casings))

    def claims(self) -> dict:
        """Regenerate the catalogue; return claim_id -> (edition, evidence sha)."""
        from . import bench_mentions as bm
        bm.introduced()
        return {c["claim_id"]: (c["edition"], hashlib.sha256(c["evidence"].encode()).hexdigest()[:16]) for c in bm.all_claims()}

    def reviewed(self) -> dict:
        from . import bench_mentions as bm
        return bm._load_review().get("claims", {})

    def build(self, publish: bool) -> dict:
        cmd = [self.python, "-m", "bench2agent", "benchmarks"] + (["--publish"] if publish else [])
        out = self._run(cmd)
        return json.loads(out.strip().splitlines()[-1])


def stale_sources(work: Work, key: str, funnel: dict, dates: dict[str, str], parser_changed: bool) -> set[str]:
    """Parsed (or permanently failed) papers whose arXiv record moved to a
    newer datestamp than the one their parse was made on, plus permanent
    failures to judge again when the parser changed. Papers whose parse
    recorded no version are not guessed stale."""
    recorded = work.source_versions(key)
    out = set()
    for base, p_ in funnel["_parsed"].items():
        at = recorded.get(base)
        if at and dates.get(base, "") > at:
            out.add(base)
        elif not p_["ok"] and not p_["transient"] and parser_changed:
            out.add(base)
    return out


def plan(work: Work, pipeline: Pipeline, editions: list[tuple[str, int]], current: dict, dates: dict[str, str] | None = None) -> list[dict]:
    """What each edition needs, from the files, the recorded versions and
    the working database's transient failures."""
    out = []
    dates = dates if dates is not None else {}
    for disp, year in editions:
        paths = edition_paths(disp, year, work.root)
        key = paths["key"]
        funnel = edition_funnel(paths)
        papers_hash = file_hash(paths["papers"])
        need = []
        norm_v, _ = work.stage_version(key, "normalized")
        if not paths["papers"].exists():
            need.append("normalize")
        elif norm_v and norm_v != current["normalizer"]:
            need.append("normalize (normalizer changed)")
        match_v, match_in = work.stage_version(key, "matched")
        if need or not paths["resolved"].exists() or match_in != papers_hash or (match_v and match_v != current["matcher"]):
            need.append("match")
        parse_v, _ = work.stage_version(key, "parsed")
        reparse = bool(parse_v and parse_v != current["parser"])
        transient = work.transients(key)
        stale = stale_sources(work, key, funnel, dates, reparse) if not reparse else set()
        if reparse:
            need.append("reparse (parser changed)")
        elif funnel["unparsed"] or funnel["parsed_transient"] or transient or stale:
            need.append(f"parse {len(funnel['unparsed'])} new, retry {len(transient) + funnel['parsed_transient']} transient, "
                        f"{len(stale)} new source versions")
        ment_v, _ = work.stage_version(key, "mentions")
        if (ment_v and ment_v != current["rules"]) or reparse:
            need.append("mentions (rules changed)" if not reparse else "mentions")
        elif funnel["mentions_missing"] or "parse" in " ".join(need):
            need.append(f"mentions ({len(funnel['mentions_missing'])} parsed papers without)")
        out.append({"edition": key, "paths": paths, "funnel": {k: v for k, v in funnel.items() if not k.startswith("_")},
                    "need": need, "papers_hash": papers_hash, "reparse": reparse, "stale": sorted(stale),
                    "transient": len(transient)})
    return out


def run(work: Work, pipeline: Pipeline, *, editions: list[tuple[str, int]] | None = None, publish: bool = False,
        dry_run: bool = False, max_parse: int = 0, discover: bool = True, recollect: str = "open") -> dict:
    """One update. With `discover`: refresh the arXiv index for the window
    since the last harvest, re-collect the open editions' lists, and treat
    a record whose datestamp moved as a new source version. Then every
    stage that the changes reach, the catalogue when anything upstream or
    the review file changed, the review queue, and a build."""
    current = versions(work.root)
    editions = editions or list(EDITIONS)
    summary = {"editions": {}, "claims": {}, "versions": current, "discovery": {}}
    if dry_run:
        dates = pipeline.index_dates()
        for p in plan(work, pipeline, editions, current, dates):
            summary["editions"][p["edition"]] = {"need": p["need"], "stale_sources": len(p["stale"]), **p["funnel"]}
        return summary
    run_id = work.start_run("update")
    try:
        touched = False
        if discover:
            today = dt.date.today().isoformat()
            since = work.get("harvest_until") or (max(pipeline.index_dates().values(), default=None))
            if since:
                since = (dt.date.fromisoformat(since[:10]) - dt.timedelta(days=1)).isoformat()   # a day of overlap
                summary["discovery"]["harvest"] = pipeline.harvest(since, today)
                work.set("harvest_until", today)
            open_keys = open_editions() if recollect == "open" else ({Corpus(d, y).key for d, y in editions} if recollect == "all" else set())
            collected = []
            for disp, year in editions:
                paths = edition_paths(disp, year, work.root)
                if paths["key"] in open_keys:
                    pipeline.collect(paths)
                    pipeline.normalize(paths)
                    work.mark_stage(paths["key"], "normalized", current["normalizer"], None, run_id)
                    collected.append(paths["key"])
            summary["discovery"]["recollected"] = collected
        dates = pipeline.index_dates()
        plans = plan(work, pipeline, editions, current, dates)
        for p in plans:
            key, paths = p["edition"], p["paths"]
            steps = p["need"]
            done = []
            if any(s.startswith("normalize") for s in steps):
                pipeline.normalize(paths)
                work.mark_stage(key, "normalized", current["normalizer"], None, run_id)
                p["papers_hash"] = file_hash(paths["papers"])
                done.append("normalize")
            if any(s.startswith("match") or s.startswith("normalize") for s in steps):
                pipeline.match(paths)
                work.mark_stage(key, "matched", current["matcher"], p["papers_hash"], run_id)
                done.append("match")
            funnel = edition_funnel(paths)
            parse_report, transient = None, []
            stale = set(p["stale"]) if "match" not in done else stale_sources(work, key, funnel, dates, p["reparse"])
            if p["reparse"] or funnel["unparsed"] or funnel["parsed_transient"] or p["transient"] or stale:
                parse_report, transient = pipeline.parse(paths, reparse=p["reparse"], limit=max_parse, drop=stale)
                work.mark_stage(key, "parsed", current["parser"], None, run_id)
                done.append("reparse" if p["reparse"] else "parse")
                funnel = edition_funnel(paths)
            if any(s.startswith("mentions") for s in steps) or any(d in done for d in ("parse", "reparse", "match")):
                if paths["fulltext"].exists():
                    pipeline.mentions(paths)
                    work.mark_stage(key, "mentions", current["rules"], None, run_id)
                    done.append("mentions")
                    touched = True
                    funnel = edition_funnel(paths)
            rows = []
            for base, p_ in funnel["_parsed"].items():
                eid = funnel["_matched"].get(base)
                if eid is None:
                    continue
                rows.append((eid, base, "parsed", "ok" if p_["ok"] else "failed", p_["error"], p_["transient"]))
            for base in funnel["unparsed"]:
                rows.append((funnel["_matched"][base], base, "matched", "pending", None, 0))
            work.record_papers(key, rows)
            # the source version each parse (or permanent failure) was made on
            work.set_source_versions(key, {b: dates[b] for b, p_ in funnel["_parsed"].items() if b in dates})
            work.record_transients(key, [(funnel["_matched"].get(b, "?"), b, err) for b, err in transient if b in funnel["_matched"]])
            summary["editions"][key] = {"did": done, **({"parse_report": parse_report} if parse_report else {}),
                                        "transient_now": len(transient), "stale_sources": len(stale),
                                        **{k: v for k, v in funnel.items() if not k.startswith("_")}}
        # claims: when any mentions changed, or the review file changed, or never compared
        review_hash = file_hash(work.root / "config" / "introduced_review.json")
        review_changed = review_hash != work.get("review_hash")
        if touched or review_changed or not work.get("claims_seen"):
            before = vocabulary_signature(work.root)
            claims = pipeline.claims()
            reviewed = pipeline.reviewed()
            seen = work.get("claims_seen") or {}
            queued, dequeued = [], []
            for cid, (ed, sha) in claims.items():
                review = reviewed.get(cid)
                if review is None:
                    if cid not in seen:
                        queued.append((cid, ed, "new", sha))
                elif review.get("evidence_sha") and review["evidence_sha"] != sha:
                    queued.append((cid, ed, "changed", sha))
                else:
                    dequeued.append(cid)                      # reviewed on this very sentence: done
            in_queue = {r[0] for r in work.con.execute("SELECT claim_id FROM claim_queue")}
            dequeued = [c for c in dequeued if c in in_queue]
            work.queue_claims(queued, run_id)
            work.dequeue_claims(dequeued)
            work.set("claims_seen", {cid: sha for cid, (_, sha) in claims.items()})
            work.set("review_hash", review_hash)
            summary["claims"] = {"total": len(claims), "queued_new": sum(1 for q in queued if q[2] == "new"),
                                 "queued_changed": sum(1 for q in queued if q[2] == "changed"),
                                 "dequeued_reviewed": len(dequeued)}
            after = vocabulary_signature(work.root)
            if after != before:
                # a confirmed new entity or a split re-keys mentions: every edition, now
                current = versions(work.root)
                for p in plans:
                    if p["paths"]["fulltext"].exists():
                        pipeline.mentions(p["paths"])
                        work.mark_stage(p["edition"], "mentions", current["rules"], None, run_id)
                summary["claims"]["mentions_rerun"] = "vocabulary changed"
                claims = pipeline.claims()
                work.set("claims_seen", {cid: sha for cid, (_, sha) in claims.items()})
        built = pipeline.build(publish)
        summary["build"] = {k: built.get(k) for k in ("revision_id", "parsed_papers", "editions", "benchmarks")}
        if publish:
            summary["published"] = (built.get("published") or {}).get("outcome")
        summary["pending_retries"] = work.pending_retries()
        work.set("pending_retries", summary["pending_retries"])
        work.finish_run(run_id, "ok", summary)
    except Exception as exc:  # noqa: BLE001 — recorded, never hidden
        work.finish_run(run_id, "failed", summary, f"{type(exc).__name__}: {exc}")
        raise
    return summary


def adopt(work: Work, editions: list[tuple[str, int]] | None = None) -> dict:
    """Record the outputs already on disk as produced by the current code,
    once, so the first incremental run does not redo every stage. It says
    what it adopted; it changes no file. Later runs trust these marks the
    way they trust their own."""
    current = versions(work.root)
    dates = index_dates(work.root)
    run_id = work.start_run("adopt")
    adopted = {}
    for disp, year in editions or list(EDITIONS):
        paths = edition_paths(disp, year, work.root)
        key = paths["key"]
        stages = []
        if paths["papers"].exists():
            work.mark_stage(key, "normalized", current["normalizer"], None, run_id)
            stages.append("normalized")
            if paths["resolved"].exists():
                work.mark_stage(key, "matched", current["matcher"], file_hash(paths["papers"]), run_id)
                stages.append("matched")
                if paths["fulltext"].exists():
                    work.mark_stage(key, "parsed", current["parser"], None, run_id)
                    stages.append("parsed")
                    if paths["mentions"].exists():
                        work.mark_stage(key, "mentions", current["rules"], None, run_id)
                        stages.append("mentions")
        funnel = edition_funnel(paths)
        rows = [(funnel["_matched"][b], b, "parsed", "ok" if p_["ok"] else "failed", p_["error"], p_["transient"])
                for b, p_ in funnel["_parsed"].items() if b in funnel["_matched"]]
        rows += [(funnel["_matched"][b], b, "matched", "pending", None, 0) for b in funnel["unparsed"]]
        work.record_papers(key, rows)
        work.set_source_versions(key, {b: dates[b] for b in funnel["_parsed"] if b in dates})
        adopted[key] = stages
    work.finish_run(run_id, "adopted", {"editions": adopted})
    work.set("claims_seen", None)      # claims are compared on the first real run
    work.set("harvest_until", max(dates.values(), default=None))
    return adopted


def status(work: Work, editions: list[tuple[str, int]] | None = None, pipeline: Pipeline | None = None) -> dict:
    current = versions(work.root)
    pipeline = pipeline or Pipeline(work)
    plans = plan(work, pipeline, editions or list(EDITIONS), current, pipeline.index_dates())
    queue = work.con.execute("SELECT reason, COUNT(*) FROM claim_queue GROUP BY reason").fetchall()
    runs = [dict(r) for r in work.con.execute("SELECT run_id, started_at, finished_at, outcome, error FROM runs ORDER BY run_id DESC LIMIT 5")]
    return {"versions": current, "last_success": work.last_success(), "harvest_until": work.get("harvest_until"),
            "pending_retries": work.pending_retries(),
            "editions": {p["edition"]: {"need": p["need"], "stale_sources": len(p["stale"]), "transient_retries": p["transient"], **p["funnel"]}
                         for p in plans},
            "claim_queue": {r[0]: r[1] for r in queue}, "recent_runs": runs}


def due(work: Work, days: float, retry_days: float = 1.0) -> bool:
    """A full refresh is due every `days`; work left over by the last run
    (transient fetches, unparsed papers) is due again after `retry_days`,
    so a daily timer retries within a day instead of next week."""
    last = work.last_success()
    if not last:
        return True
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last)).total_seconds()
    if age >= days * 86400:
        return True
    return bool(work.pending_retries()) and age >= retry_days * 86400


class Locked:
    """One update at a time; a second caller sees who holds it and leaves."""

    def __init__(self, root: Path = ROOT):
        self.path = work_dir(root) / "update.lock"
        self.fh = None

    def __enter__(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = self.path.open("a+")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.seek(0)
            holder = self.fh.read().strip()
            self.fh.close()
            raise RuntimeError(f"another update is running ({holder or 'holder unknown'})") from None
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(f"pid {os.getpid()} started {now()}\n")
        self.fh.flush()
        return self

    def __exit__(self, *exc):
        import fcntl
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["status", "run", "adopt"])
    ap.add_argument("--edition", action="append", default=[], help="venue-year, e.g. iclr-2025 (repeatable)")
    ap.add_argument("--publish", action="store_true", help="install the built revision as the active one")
    ap.add_argument("--dry-run", action="store_true", help="print what would run")
    ap.add_argument("--if-due", type=float, default=None, metavar="DAYS", help="run only when the last success is older")
    ap.add_argument("--max-parse", type=int, default=0, help="fetch at most this many new papers per edition")
    ap.add_argument("--retry-days", type=float, default=1.0, help="with --if-due: leftover work is due again after this many days")
    ap.add_argument("--no-discover", action="store_true", help="skip the arXiv index refresh and the re-collection of open editions")
    ap.add_argument("--recollect", choices=["open", "all", "none"], default="open", help="which editions' lists to re-fetch")
    args = ap.parse_args()
    editions = None
    if args.edition:
        by_key = {Corpus(d, y).key: (d, y) for d, y in EDITIONS}
        editions = [by_key[e] for e in args.edition]
    work = Work()
    if args.command == "status":
        print(json.dumps(status(work, editions), ensure_ascii=False, indent=1, default=str))
        return 0
    if args.command == "adopt":
        print(json.dumps(adopt(work, editions), ensure_ascii=False, indent=1))
        return 0
    if args.if_due is not None and not due(work, args.if_due, args.retry_days):
        print(f"not due: last success {work.last_success()}, pending retries {work.pending_retries()}")
        return 0
    try:
        with Locked():
            summary = run(work, Pipeline(work), editions=editions, publish=args.publish, dry_run=args.dry_run, max_parse=args.max_parse,
                          discover=not args.no_discover, recollect=args.recollect)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
