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
CREATE TABLE IF NOT EXISTS claim_history (
    claim_id TEXT PRIMARY KEY, edition TEXT NOT NULL, arxiv_base TEXT, name TEXT, noun TEXT, title TEXT,
    evidence TEXT, evidence_sha TEXT NOT NULL, first_seen_run INTEGER, last_seen_run INTEGER, vanished_run INTEGER, vanished_at TEXT);
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
        for col in ("source_version", "content_sha256", "baseline_datestamp", "recheck_datestamp", "recheck_status", "recheck_error", "last_recheck_at"):
            if col not in cols:
                self.con.execute(f"ALTER TABLE paper_stages ADD COLUMN {col} TEXT")
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
        return self.con.execute("SELECT COUNT(*) FROM paper_stages WHERE status IN ('transient', 'pending') "
                                "OR recheck_status = 'failed'").fetchone()[0]

    def versions_of(self, edition: str) -> dict[str, dict]:
        """arxiv_base -> what is known about the text behind its parse:
        `source_version` (the OAI datestamp confirmed when the text was
        fetched, None for a parse of unknown provenance), `content_sha256`
        (the parsed text), `baseline_datestamp` (the stamp the paper was
        last checked against), and the last re-check outcome."""
        return {r["arxiv_base"]: dict(r) for r in self.con.execute(
            "SELECT arxiv_base, source_version, content_sha256, baseline_datestamp, recheck_datestamp, recheck_status, last_recheck_at "
            "FROM paper_stages WHERE edition = ? AND arxiv_base IS NOT NULL", (edition,))}

    def set_versions(self, edition: str, confirmed: dict[str, tuple[str | None, str | None]]) -> None:
        """Record a confirmed fetch: (datestamp, content hash) per base. Only
        a parse made in this run on text fetched now is confirmed."""
        ts = now()
        self.con.executemany("UPDATE paper_stages SET source_version = ?, content_sha256 = ?, baseline_datestamp = ?, "
                             "recheck_status = NULL, recheck_datestamp = NULL, last_recheck_at = ? WHERE edition = ? AND arxiv_base = ?",
                             [(ds, sha, ds, ts, edition, b) for b, (ds, sha) in confirmed.items()])
        self.con.commit()

    def set_baselines(self, edition: str, stamps: dict[str, str], only_missing: bool = True) -> None:
        """The datestamp a paper was last checked against (a signal, not a
        version): set once for adopted rows, moved on every re-check."""
        self.con.executemany("UPDATE paper_stages SET baseline_datestamp = ? WHERE edition = ? AND arxiv_base = ?"
                             + (" AND baseline_datestamp IS NULL" if only_missing else ""),
                             [(ds, edition, b) for b, ds in stamps.items()])
        self.con.commit()

    def set_recheck(self, edition: str, base: str, datestamp: str, status: str, error: str | None = None) -> None:
        """The latest attempt to confirm a paper's text against a moved
        record, kept apart from the text it serves: `failed` (transient,
        retried next run) or `unavailable` (no HTML or a thin page now;
        judged again when the record or the parser changes, or after
        `recheck_days`). The served text, its hash and version stay.
        `last_recheck_at` moves only here and on a fetch: `updated_at` moves
        on every run that records the paper, so a period measured from it
        would slide forever."""
        ts = now()
        self.con.execute("UPDATE paper_stages SET recheck_datestamp = ?, recheck_status = ?, recheck_error = ?, updated_at = ?, last_recheck_at = ? "
                         "WHERE edition = ? AND arxiv_base = ?", (datestamp, status, error, ts, ts, edition, base))
        self.con.commit()

    def recheck_failures(self, edition: str) -> list[str]:
        return [r[0] for r in self.con.execute(
            "SELECT arxiv_base FROM paper_stages WHERE edition = ? AND recheck_status = 'failed'", (edition,))]

    def rechecks_to_rejudge(self, edition: str, dates: dict[str, str], parser_changed: bool, recheck_days: float) -> list[str]:
        """`unavailable` re-checks due again: the record moved past the
        stamp tried, the parser changed, or the last ATTEMPT
        (`last_recheck_at`, never `updated_at`) is older than `recheck_days`."""
        out = []
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=recheck_days)).isoformat(timespec="seconds")
        for r in self.con.execute("SELECT arxiv_base, recheck_datestamp, last_recheck_at FROM paper_stages WHERE edition = ? AND recheck_status = 'unavailable'", (edition,)):
            if parser_changed or dates.get(r[0], "") > (r[1] or "") or (r[2] or "") < cutoff:
                out.append(r[0])
        return out

    def record_claims(self, claims: dict, run_id: int) -> tuple[list[str], list[str]]:
        """Keep every claim ever extracted with its last sentence; returns
        (new claim ids, vanished claim ids) against the previous run. A
        vanished claim keeps its last sentence and is marked, never deleted."""
        seen_before = {r[0]: r[1] for r in self.con.execute("SELECT claim_id, vanished_run FROM claim_history")}
        new_ids, ts = [], now()
        for cid, c in claims.items():
            if cid not in seen_before:
                new_ids.append(cid)
            self.con.execute(
                "INSERT INTO claim_history (claim_id, edition, arxiv_base, name, noun, title, evidence, evidence_sha, first_seen_run, last_seen_run) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(claim_id) DO UPDATE SET edition = excluded.edition, name = excluded.name, "
                "noun = excluded.noun, title = excluded.title, evidence = excluded.evidence, evidence_sha = excluded.evidence_sha, "
                "last_seen_run = excluded.last_seen_run, vanished_run = NULL, vanished_at = NULL",
                (cid, c["edition"], c.get("arxiv_base"), c.get("name"), c.get("noun"), c.get("title"), c.get("evidence"), c["sha"], run_id, run_id))
        vanished = [cid for cid, v in seen_before.items() if cid not in claims and v is None]
        self.con.executemany("UPDATE claim_history SET vanished_run = ?, vanished_at = ? WHERE claim_id = ?", [(run_id, ts, cid) for cid in vanished])
        self.con.commit()
        return new_ids, vanished

    def has_paper(self, edition: str, base: str) -> bool:
        return self.con.execute("SELECT 1 FROM paper_stages WHERE edition = ? AND arxiv_base = ?", (edition, base)).fetchone() is not None

    def claim_record(self, cid: str) -> dict | None:
        r = self.con.execute("SELECT * FROM claim_history WHERE claim_id = ?", (cid,)).fetchone()
        return dict(r) if r else None

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


def index_state(root: Path = ROOT) -> dict:
    """The arXiv index as the run sees it: `stamps` (arxiv_base -> newest
    record stamp: the OAI header datestamp, or the dc:date of rows
    harvested before it was kept) and `hash`, the SHA-256 of the index file,
    computed once per run and shared by every edition."""
    path = root / "data/raw/arxiv/oai_index.jsonl"
    out: dict[str, str] = {}
    if not path.exists():
        return {"stamps": out, "hash": None}
    for r in read_jsonl(path):
        d = r.get("datestamp") or r.get("date") or ""
        if d >= out.get(r["arxiv_base"], ""):
            out[r["arxiv_base"]] = d
    return {"stamps": out, "hash": file_hash(path)}


def index_dates(root: Path = ROOT) -> dict[str, str]:
    return index_state(root)["stamps"]


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
    rows_by_base: dict[str, dict] = {}
    if paths["fulltext"].exists():
        for r in read_jsonl(paths["fulltext"]):
            parsed[r["arxiv_base"]] = {"ok": bool(r.get("ok")), "error": r.get("error"), "transient": bool(r.get("transient"))}
            rows_by_base[r["arxiv_base"]] = {"html_sha256": r.get("html_sha256"), "arxiv_version": r.get("arxiv_version")}
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
            "_papers": papers, "_matched": matched, "_parsed": parsed, "_rows": rows_by_base}


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

    def index_state(self) -> dict:
        return index_state(self.work.root)

    def index_dates(self) -> dict[str, str]:
        return self.index_state()["stamps"]

    def recheck(self, paths: dict, bases: list[str]) -> tuple[list[dict], list[tuple[str, str]]]:
        """Fetch and parse the given papers again into a temporary output and
        archive, touching nothing of the edition's files. Returns the rows
        html_extract produced (ok or not, each with `html_sha256` of the
        text it read) and the transient failures it reported."""
        if not self.parser_python:
            raise RuntimeError("no parser environment: lxml is not importable and BENCH2AGENT_PARSER_PYTHON is not set")
        if not Path(self.parser_python).exists():
            raise RuntimeError(f"parser environment not found: {self.parser_python} (BENCH2AGENT_PARSER_PYTHON)")
        scratch = work_dir(self.work.root) / "recheck"
        scratch.mkdir(parents=True, exist_ok=True)
        resolved = scratch / f"resolved_{paths['key']}.jsonl"
        out = scratch / f"fulltext_{paths['key']}.jsonl"
        archive = scratch / "archive"
        archive.mkdir(exist_ok=True)
        wanted = set(bases)
        with resolved.open("w", encoding="utf-8") as fh:
            for r in read_jsonl(paths["resolved"]):
                if r.get("arxiv_base") in wanted:
                    fh.write(json.dumps(r) + "\n")
        if out.exists():
            out.unlink()
        for b in wanted:
            cached = archive / f"{b}.html.gz"
            if cached.exists():
                cached.unlink()
        text = self._run([self.parser_python, "-m", "icml.html_extract", "--resolved", str(resolved), "--out", str(out),
                          "--archive", str(archive)])
        rows = list(read_jsonl(out)) if out.exists() else []
        transient = [(l.split()[1], " ".join(l.split()[2:])) for l in text.splitlines() if l.startswith("transient ") and len(l.split()) >= 2]
        return rows, transient

    def normalize(self, paths: dict) -> None:
        self._run([self.python, "-m", "icml.normalize", "--venue", paths["venue"], "--year", str(paths["year"]), "--allow-missing-abstracts"])

    def match(self, paths: dict) -> None:
        self._run([self.python, "-m", "icml.arxiv_match", "--venue", paths["venue"], "--year", str(paths["year"])])

    def parse(self, paths: dict, *, reparse: bool, limit: int = 0) -> tuple[str, list[tuple[str, str]]]:
        """html_extract appends rows for every resolved base absent from the
        output; transiently failed rows (none are written by current
        html_extract, older files may hold some) are dropped first so they
        are fetched again; under `reparse` every row is rebuilt from the
        archive. A paper's text is never deleted here: a new version is
        handled by `recheck`, which fetches into a temporary path first.
        Returns html_extract's final summary line and the transient
        failures it reported, which it never records as rows."""
        if not self.parser_python:
            raise RuntimeError("no parser environment: lxml is not importable and BENCH2AGENT_PARSER_PYTHON is not set")
        if not Path(self.parser_python).exists():
            raise RuntimeError(f"parser environment not found: {self.parser_python} (BENCH2AGENT_PARSER_PYTHON)")
        ft = paths["fulltext"]
        if ft.exists():
            rows = [] if reparse else [r for r in read_jsonl(ft) if not (not r.get("ok") and r.get("transient"))]
            tmp = ft.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmp.replace(ft)
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
        """Regenerate the catalogue; return claim_id -> the claim as extracted
        now (edition, sentence hash, sentence, name, noun, paper, title)."""
        from . import bench_mentions as bm
        bm.introduced()
        return {c["claim_id"]: {"edition": c["edition"], "sha": hashlib.sha256(c["evidence"].encode()).hexdigest()[:16],
                                "evidence": c["evidence"], "name": c["name"], "noun": c["noun"], "arxiv_base": c["arxiv_base"],
                                "title": c["title"]} for c in bm.all_claims()}

    def reviewed(self) -> dict:
        from . import bench_mentions as bm
        return bm._load_review().get("claims", {})

    def retain_vanished(self, cid: str, record: dict) -> None:
        """A reviewed claim's sentence is gone from the latest text: keep its
        last sentence on the review entry, so the catalogue keeps the
        benchmark with that claim marked as vanished until a reviewer
        decides retain or retire (`docs/introduced-review.md`)."""
        from . import bench_mentions as bm
        rev = bm._load_review()
        entry = rev["claims"].get(cid)
        if entry is None:
            return
        entry["retained_evidence"] = {k: record.get(k) for k in ("edition", "arxiv_base", "name", "noun", "title", "evidence", "evidence_sha", "reason")}
        # what was known of the paper when the claim was last extracted stays with it
        p = bm.paper_index().get(record.get("arxiv_base") or "", {})
        entry["retained_evidence"]["title"] = record.get("title") or p.get("title") or ""
        entry["retained_evidence"]["authors"] = list(p.get("authors") or [])
        entry["retained_evidence"]["vanished_at"] = record.get("vanished_at") or now()
        bm.dump_json(bm.REVIEW, rev)

    def build(self, publish: bool) -> dict:
        cmd = [self.python, "-m", "bench2agent", "benchmarks"] + (["--publish"] if publish else [])
        out = self._run(cmd)
        return json.loads(out.strip().splitlines()[-1])


def replace_rows(ft: Path, new_rows: dict[str, dict]) -> None:
    """Swap the given papers' rows in an edition's full-text file, atomically."""
    tmp = ft.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        if ft.exists():
            for r in read_jsonl(ft):
                if r["arxiv_base"] not in new_rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        for r in new_rows.values():
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(ft)


def archive_version(archive: Path, base: str, fetched: Path, sha: str) -> None:
    """Keep the fetched HTML under its content hash and as the current copy
    (`<base>.<sha12>.html.gz`, `<base>.html.gz`); an earlier current copy
    is kept under its own hash if it is not there yet."""
    import gzip
    import hashlib as _h
    current = archive / f"{base}.html.gz"
    if current.exists():
        old_sha = _h.sha256(gzip.decompress(current.read_bytes())).hexdigest()
        keep = archive / f"{base}.{old_sha[:12]}.html.gz"
        if not keep.exists():
            os.replace(current, keep)
    versioned = archive / f"{base}.{sha[:12]}.html.gz"
    if not versioned.exists():
        os.replace(fetched, versioned)
    else:
        fetched.unlink()
    import shutil
    shutil.copyfile(versioned, current)


def archived_sha256(archive: Path, base: str) -> str | None:
    import gzip
    import hashlib as _h
    current = archive / f"{base}.html.gz"
    if not current.exists():
        return None
    try:
        return _h.sha256(gzip.decompress(current.read_bytes())).hexdigest()
    except OSError:
        return None


RECHECK_DAYS = 30.0


def stale_sources(work: Work, key: str, funnel: dict, dates: dict[str, str], parser_changed: bool, recheck_days: float = RECHECK_DAYS) -> set[str]:
    """Papers to check again: their arXiv record's stamp moved past the
    stamp their text was confirmed on (`source_version`) or last checked
    against (`baseline_datestamp`, and for an `unavailable` re-check the
    stamp tried), a re-check failed transiently last time, an `unavailable`
    re-check is due again (parser changed, or older than `recheck_days`), or
    they failed permanently and the parser changed. A row with no stamp at
    all is of unknown provenance and is not judged stale by a stamp."""
    known = work.versions_of(key)
    out = set(work.recheck_failures(key)) | set(work.rechecks_to_rejudge(key, dates, parser_changed, recheck_days))
    for base, p_ in funnel["_parsed"].items():
        v = known.get(base) or {}
        if v.get("recheck_status") == "unavailable":
            continue                        # judged by rechecks_to_rejudge above, against the stamp tried
        at = v.get("source_version") or v.get("baseline_datestamp")
        if at and dates.get(base, "") > at:
            out.add(base)
        elif not p_["ok"] and not p_["transient"] and parser_changed:
            out.add(base)
    return out


def plan(work: Work, pipeline: Pipeline, editions: list[tuple[str, int]], current: dict, index: dict | None = None,
         recheck_days: float = RECHECK_DAYS) -> list[dict]:
    """What each edition needs, from the files, the recorded versions and
    the working database's transient failures. `index` is `index_state()`:
    a changed index re-matches every edition, so unmatched, ambiguous,
    collided and fuzzily matched papers meet the new records."""
    out = []
    index = index or {"stamps": {}, "hash": None}
    dates = index["stamps"]
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
        match_input = f"{papers_hash}|{index['hash']}"
        if need or not paths["resolved"].exists() or match_in != match_input or (match_v and match_v != current["matcher"]):
            need.append("match (index changed)" if match_in and match_in.split("|")[0] == papers_hash and not need else "match")
        parse_v, _ = work.stage_version(key, "parsed")
        reparse = bool(parse_v and parse_v != current["parser"])
        transient = work.transients(key)
        stale = stale_sources(work, key, funnel, dates, reparse, recheck_days) if not reparse else set()
        if reparse:
            need.append("reparse (parser changed)")
        elif funnel["unparsed"] or funnel["parsed_transient"] or transient or stale:
            need.append(f"parse {len(funnel['unparsed'])} new, retry {len(transient) + funnel['parsed_transient']} transient, "
                        f"re-check {len(stale)} moved records")
        ment_v, _ = work.stage_version(key, "mentions")
        if (ment_v and ment_v != current["rules"]) or reparse:
            need.append("mentions (rules changed)" if not reparse else "mentions")
        elif funnel["mentions_missing"] or funnel["unparsed"] or funnel["parsed_transient"] or transient:
            # new text will be parsed; a re-check alone re-runs mentions only when the text changed
            need.append(f"mentions ({len(funnel['mentions_missing'])} parsed papers without)")
        out.append({"edition": key, "paths": paths, "funnel": {k: v for k, v in funnel.items() if not k.startswith("_")},
                    "need": need, "papers_hash": papers_hash, "match_input": match_input, "reparse": reparse,
                    "stale": sorted(stale), "transient": len(transient)})
    return out


def run(work: Work, pipeline: Pipeline, *, editions: list[tuple[str, int]] | None = None, publish: bool = False,
        dry_run: bool = False, max_parse: int = 0, discover: bool = True, recollect: str = "open",
        recheck_days: float = RECHECK_DAYS) -> dict:
    """One update. With `discover`: refresh the arXiv index for the window
    since the last harvest, re-collect the open editions' lists, and treat
    a record whose datestamp moved as a new source version. Then every
    stage that the changes reach, the catalogue when anything upstream or
    the review file changed, the review queue, and a build."""
    current = versions(work.root)
    editions = editions or list(EDITIONS)
    summary = {"editions": {}, "claims": {}, "versions": current, "discovery": {}}
    if dry_run:
        for p in plan(work, pipeline, editions, current, pipeline.index_state(), recheck_days):
            summary["editions"][p["edition"]] = {"need": p["need"], "recheck": len(p["stale"]), **p["funnel"]}
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
        index = pipeline.index_state()
        dates = index["stamps"]
        plans = plan(work, pipeline, editions, current, index, recheck_days)
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
                work.mark_stage(key, "matched", current["matcher"], f"{p['papers_hash']}|{index['hash']}", run_id)
                done.append("match")
            funnel = edition_funnel(paths)
            before_parse = set(funnel["_parsed"])
            parse_report, transient = None, []
            if p["reparse"] or funnel["unparsed"] or funnel["parsed_transient"] or p["transient"]:
                parse_report, transient = pipeline.parse(paths, reparse=p["reparse"], limit=max_parse)
                work.mark_stage(key, "parsed", current["parser"], None, run_id)
                done.append("reparse" if p["reparse"] else "parse")
                funnel = edition_funnel(paths)
            # papers whose arXiv record moved: fetched again into a temporary
            # path and compared with the text the current parse read; the
            # edition's files change only for a text that differs (or a
            # failed/unknown one), and never when the fetch fails
            stale = set(p["stale"]) if "match" not in done else stale_sources(work, key, funnel, dates, p["reparse"], recheck_days)
            stale -= set(funnel["unparsed"])
            recheck = {"same": 0, "changed": 0, "failed": 0, "unavailable": 0, "unknown": 0}
            confirmed: dict[str, tuple[str | None, str | None]] = {}
            if stale:                       # after a reparse too: the archive holds the old text, the record moved
                rows, r_transient = pipeline.recheck(paths, sorted(stale))
                got = {r["arxiv_base"]: r for r in rows}
                replace = {}
                known = work.versions_of(key)
                for base in sorted(stale):
                    stamp = dates.get(base, "")
                    r = got.get(base)
                    current_ok = funnel["_parsed"].get(base, {}).get("ok")
                    if r is None or (not r.get("ok") and r.get("transient")):
                        work.set_recheck(key, base, stamp, "failed", (r or {}).get("error") or "not fetched")
                        recheck["failed"] += 1
                        continue
                    if not r.get("ok"):
                        # the latest text is not there (no HTML, a thin page): the served
                        # parse stays, with its own hash and version; the attempt is recorded
                        if current_ok:
                            work.set_recheck(key, base, stamp, "unavailable", r.get("error"))
                            recheck["unavailable"] += 1
                            continue
                        replace[base] = r                                        # a failed parse replaced by a failed parse: recorded as such
                        confirmed[base] = (stamp, r.get("html_sha256"))
                        recheck["unknown"] += 1
                        continue
                    previous = (known.get(base) or {}).get("content_sha256") or archived_sha256(paths["archive"], base)
                    if current_ok and previous and r.get("html_sha256") == previous:
                        work.set_versions(key, {base: (stamp, previous)})           # text unchanged: the stamp is confirmed for it
                        recheck["same"] += 1
                        continue
                    replace[base] = r
                    confirmed[base] = (stamp, r.get("html_sha256"))
                    recheck["changed" if previous else "unknown"] += 1
                    fetched = work_dir(work.root) / "recheck" / "archive" / f"{base}.html.gz"
                    if fetched.exists() and r.get("html_sha256"):
                        archive_version(paths["archive"], base, fetched, r["html_sha256"])
                if replace:
                    replace_rows(paths["fulltext"], replace)
                    done.append(f"recheck {len(replace)} replaced")
                    funnel = edition_funnel(paths)
                for base, err in r_transient:
                    work.set_recheck(key, base, dates.get(base, ""), "failed", err)
            new_parses = set(funnel["_parsed"]) - before_parse if not p["reparse"] else set()
            for base in new_parses:
                row = funnel["_rows"].get(base) or {}
                confirmed[base] = (dates.get(base), row.get("html_sha256"))
            # mentions read the parsed text only: a re-match alone (no new text) does not change them
            if any(s.startswith("mentions") for s in steps) or any(d in ("parse", "reparse") or d.startswith("recheck") for d in done):
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
            # only a text fetched in this run has a confirmed version; everything else keeps what it had
            work.set_versions(key, {b: v for b, v in confirmed.items() if b in funnel["_matched"]})
            work.record_transients(key, [(funnel["_matched"].get(b, "?"), b, err) for b, err in transient if b in funnel["_matched"]])
            summary["editions"][key] = {"did": done, **({"parse_report": parse_report} if parse_report else {}),
                                        "transient_now": len(transient), "recheck": recheck, "confirmed_versions": len(confirmed),
                                        **{k: v for k, v in funnel.items() if not k.startswith("_")}}
        # claims: when any mentions changed, or the review file changed, or never compared
        review_hash = file_hash(work.root / "config" / "introduced_review.json")
        review_changed = review_hash != work.get("review_hash")
        if touched or review_changed or not work.get("claims_seen"):
            before = vocabulary_signature(work.root)
            claims = pipeline.claims()
            reviewed = pipeline.reviewed()
            new_ids, vanished = work.record_claims(claims, run_id)
            seen = work.get("claims_seen") or {}
            queued, dequeued = [], []
            for cid, c in claims.items():
                ed, sha = c["edition"], c["sha"]
                review = reviewed.get(cid)
                if review is None:
                    if cid not in seen:                       # new since the previous comparison, never re-queued by the history
                        queued.append((cid, ed, "new", sha))
                elif review.get("evidence_sha") and review["evidence_sha"] != sha:
                    queued.append((cid, ed, "changed", sha))
                else:
                    dequeued.append(cid)                      # reviewed on this very sentence: done
            # a claim that is no longer extracted: queued with its last sentence;
            # a reviewed one keeps its benchmark listed, marked, until a reviewer decides
            for cid in vanished:
                record = work.claim_record(cid) or {}
                # why: the paper left the edition's matched set (identity or match
                # change), or it is still there and the sentence is not extracted
                # any more (text, parser or rule change)
                record["reason"] = ("sentence_not_extracted" if work.has_paper(record.get("edition") or "", record.get("arxiv_base") or "")
                                    else "paper_unmatched")
                queued.append((cid, record.get("edition") or "?", "vanished", record.get("evidence_sha") or ""))
                if reviewed.get(cid, {}).get("status") == "confirmed":
                    pipeline.retain_vanished(cid, record)
            if vanished:
                reviewed = pipeline.reviewed()
                if any(reviewed.get(cid, {}).get("retained_evidence") for cid in vanished):
                    pipeline.claims()                         # the catalogue with the retained claims marked
            # a vanished claim leaves the queue once a reviewer decided retain or
            # retire (`vanished_decision`); its history row stays as it is
            for (cid,) in work.con.execute("SELECT claim_id FROM claim_queue WHERE reason = 'vanished'").fetchall():
                if (reviewed.get(cid, {}).get("vanished_decision") or {}).get("decision") in ("retain", "retire"):
                    dequeued.append(cid)
            in_queue = {r[0] for r in work.con.execute("SELECT claim_id FROM claim_queue")}
            dequeued = [c for c in dequeued if c in in_queue]
            work.queue_claims(queued, run_id)
            work.dequeue_claims(dequeued)
            work.set("claims_seen", {cid: c["sha"] for cid, c in claims.items()})
            work.set("review_hash", file_hash(work.root / "config" / "introduced_review.json"))
            summary["claims"] = {"total": len(claims), "queued_new": sum(1 for q in queued if q[2] == "new"),
                                 "queued_changed": sum(1 for q in queued if q[2] == "changed"),
                                 "queued_vanished": sum(1 for q in queued if q[2] == "vanished"),
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
                work.set("claims_seen", {cid: c["sha"] for cid, c in claims.items()})
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
    index = index_state(work.root)
    dates = index["hash"] and index["stamps"] or {}
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
                work.mark_stage(key, "matched", current["matcher"], f"{file_hash(paths['papers'])}|{index['hash']}", run_id)
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
        # adopted parses have no confirmed version: only the stamp they are checked against from now on
        work.set_baselines(key, {b: dates[b] for b in funnel["_parsed"] if b in dates})
        adopted[key] = stages
    work.finish_run(run_id, "adopted", {"editions": adopted})
    work.set("claims_seen", None)      # claims are compared on the first real run
    work.set("harvest_until", max(dates.values(), default=None))
    return adopted


def status(work: Work, editions: list[tuple[str, int]] | None = None, pipeline: Pipeline | None = None) -> dict:
    current = versions(work.root)
    pipeline = pipeline or Pipeline(work)
    plans = plan(work, pipeline, editions or list(EDITIONS), current, pipeline.index_state())
    queue = work.con.execute("SELECT reason, COUNT(*) FROM claim_queue GROUP BY reason").fetchall()
    unavailable = work.con.execute("SELECT COUNT(*) FROM paper_stages WHERE recheck_status = 'unavailable'").fetchone()[0]
    runs = [dict(r) for r in work.con.execute("SELECT run_id, started_at, finished_at, outcome, error FROM runs ORDER BY run_id DESC LIMIT 5")]
    return {"versions": current, "last_success": work.last_success(), "harvest_until": work.get("harvest_until"),
            "pending_retries": work.pending_retries(), "recheck_unavailable": unavailable,
            "editions": {p["edition"]: {"need": p["need"], "recheck": len(p["stale"]), "transient_retries": p["transient"], **p["funnel"]}
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
    ap.add_argument("--recheck-days", type=float, default=RECHECK_DAYS, help="an unavailable latest text is tried again after this many days")
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
                          discover=not args.no_discover, recollect=args.recollect, recheck_days=args.recheck_days)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
