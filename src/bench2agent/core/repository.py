"""Storage behind the six benchmark tools: one contract, two backends.

The JSON snapshot (`benchmark_snapshot.json`) stays the reference. The SQLite
file (`benchmark_snapshot.sqlite`) is imported from it, exports back to an
identical snapshot, and answers the same queries without loading the whole
corpus into memory. Both implement the same small contract, so every count,
ordering and evidence rule lives once, in `BenchmarkStore`.

What the import preserves, row for row: every parsed observation (including
papers with no stated use), benchmark ids with aliases, merged ids and
homonym relations, claims and developer lists, each evidence record in its
order, unresolved attribution, field labels, resource locations with their
check flags, edition coverage and the snapshot's own identity. Residual fields
the schema does not model go to an `extra` JSON column, never dropped.

Identity is kept apart on purpose: `snapshot_id` names the extraction the
data came from; `db_schema_version` names this file layout; the artifact
checksum names these exact bytes. Dates are copied, never invented.
"""
from __future__ import annotations

import collections
import contextlib
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
from pathlib import Path

SNAPSHOT = "benchmark_snapshot.json"
DATABASE = "benchmark_snapshot.sqlite"
SOURCE = "arxiv_html_stated_roles"
DB_SCHEMA_VERSION = 1
ROLES = ("evaluates_on", "trains_on")
BACKENDS = ("json", "sqlite", "auto")

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- value is JSON
CREATE TABLE editions (
    edition TEXT PRIMARY KEY, venue TEXT NOT NULL, year INTEGER NOT NULL,
    total_papers INTEGER, arxiv_matched INTEGER, parsed_papers INTEGER, field_labelled_papers INTEGER,
    extra TEXT);
CREATE TABLE benchmarks (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT, fold TEXT, registered TEXT,
    homonym_of_registered TEXT, first_claim TEXT, hf TEXT, gh TEXT, url TEXT, ok TEXT,
    keys TEXT NOT NULL, extra TEXT);
CREATE TABLE benchmark_aliases (benchmark_id TEXT NOT NULL REFERENCES benchmarks(id), ord INTEGER NOT NULL, alias TEXT NOT NULL,
    PRIMARY KEY (benchmark_id, ord));
CREATE TABLE benchmark_merged_ids (benchmark_id TEXT NOT NULL REFERENCES benchmarks(id), ord INTEGER NOT NULL, old_id TEXT NOT NULL,
    PRIMARY KEY (benchmark_id, ord));
CREATE TABLE benchmark_homonyms (benchmark_id TEXT NOT NULL REFERENCES benchmarks(id), ord INTEGER NOT NULL, other_id TEXT NOT NULL,
    PRIMARY KEY (benchmark_id, ord));
CREATE TABLE benchmark_developers (benchmark_id TEXT NOT NULL REFERENCES benchmarks(id), ord INTEGER NOT NULL, name TEXT NOT NULL,
    PRIMARY KEY (benchmark_id, ord));
CREATE TABLE claims (
    benchmark_id TEXT NOT NULL REFERENCES benchmarks(id), ord INTEGER NOT NULL, claim_id TEXT,
    arxiv_base TEXT NOT NULL, edition TEXT NOT NULL, title TEXT, noun TEXT, evidence TEXT, label TEXT, extra TEXT,
    PRIMARY KEY (benchmark_id, ord));
CREATE TABLE observations (
    obs_id INTEGER PRIMARY KEY, ord INTEGER NOT NULL UNIQUE, paper_id TEXT NOT NULL,
    edition TEXT NOT NULL REFERENCES editions(edition), title TEXT, keys TEXT NOT NULL, extra TEXT,
    UNIQUE (paper_id, edition));
CREATE TABLE observation_authors (obs_id INTEGER NOT NULL REFERENCES observations(obs_id), ord INTEGER NOT NULL, name TEXT NOT NULL,
    PRIMARY KEY (obs_id, ord));
CREATE TABLE observation_fields (obs_id INTEGER NOT NULL REFERENCES observations(obs_id), ord INTEGER NOT NULL,
    label TEXT NOT NULL, label_cf TEXT NOT NULL, PRIMARY KEY (obs_id, ord));
CREATE TABLE uses (
    use_id INTEGER PRIMARY KEY, obs_id INTEGER NOT NULL REFERENCES observations(obs_id), ord INTEGER NOT NULL,
    benchmark_id TEXT NOT NULL REFERENCES benchmarks(id), keys TEXT NOT NULL, extra TEXT, UNIQUE (obs_id, benchmark_id));
CREATE TABLE use_surfaces (use_id INTEGER NOT NULL REFERENCES uses(use_id), ord INTEGER NOT NULL, surface TEXT NOT NULL, n INTEGER NOT NULL,
    PRIMARY KEY (use_id, ord));
CREATE TABLE use_roles (use_id INTEGER NOT NULL REFERENCES uses(use_id), ord INTEGER NOT NULL, role TEXT NOT NULL, n_evidence INTEGER NOT NULL,
    PRIMARY KEY (use_id, ord), UNIQUE (use_id, role));
CREATE TABLE evidence (
    evidence_id INTEGER PRIMARY KEY, use_id INTEGER NOT NULL REFERENCES uses(use_id), role TEXT NOT NULL,
    ord INTEGER NOT NULL, place TEXT, text TEXT, extra TEXT, UNIQUE (use_id, role, ord));
CREATE TABLE unattributed (
    unattributed_id INTEGER PRIMARY KEY, obs_id INTEGER NOT NULL REFERENCES observations(obs_id), ord INTEGER NOT NULL,
    key TEXT, name TEXT, role TEXT, reason TEXT, keys TEXT NOT NULL, extra TEXT, UNIQUE (obs_id, ord));
CREATE TABLE unattributed_candidates (unattributed_id INTEGER NOT NULL REFERENCES unattributed(unattributed_id), ord INTEGER NOT NULL,
    benchmark_id TEXT NOT NULL REFERENCES benchmarks(id), PRIMARY KEY (unattributed_id, ord));
"""
INDEXES = """
CREATE INDEX ix_observations_edition ON observations(edition, ord);
CREATE INDEX ix_observations_paper ON observations(paper_id);
CREATE INDEX ix_fields_label ON observation_fields(label_cf, obs_id);
CREATE INDEX ix_uses_benchmark ON uses(benchmark_id, obs_id);
CREATE INDEX ix_evidence_use ON evidence(use_id, role, ord);
CREATE INDEX ix_claims_paper ON claims(arxiv_base);
CREATE INDEX ix_candidates_benchmark ON unattributed_candidates(benchmark_id);
"""

_BENCH_COLUMNS = ("name", "kind", "fold", "registered", "homonym_of_registered", "first_claim", "hf", "gh", "url")
_BENCH_CHILDREN = ("aliases", "merged_ids", "homonym_of", "developers", "claims")
_CLAIM_COLUMNS = ("claim_id", "arxiv_base", "edition", "title", "noun", "evidence", "label")
_EDITION_COLUMNS = ("total_papers", "arxiv_matched", "parsed_papers", "field_labelled_papers")
_UNATTRIBUTED_COLUMNS = ("key", "name", "role", "reason")


def _j(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


def _residual(entry: dict, known) -> str | None:
    rest = {k: v for k, v in entry.items() if k not in known}
    return _j(rest) if rest else None


def validate_snapshot(data) -> dict:
    """The snapshot's shape, before anything reads or imports it."""
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("source") != SOURCE:
        raise ValueError("Unsupported benchmark snapshot schema or extraction source.")
    if not isinstance(data.get("snapshot_id"), str) or not data["snapshot_id"]:
        raise ValueError("Snapshot identity is missing.")
    for field, kind in (("editions", dict), ("benchmarks", list), ("papers", list)):
        if not isinstance(data.get(field), kind):
            raise ValueError(f"Invalid snapshot field: {field}")
    editions = data["editions"]
    ids = {e["id"] for e in data["benchmarks"] if isinstance(e, dict) and isinstance(e.get("id"), str)}
    if len(ids) != len(data["benchmarks"]):
        raise ValueError("Invalid or duplicate benchmark identities.")
    for paper in data["papers"]:
        if not isinstance(paper, dict) or paper.get("edition") not in editions or not isinstance(paper.get("uses"), dict):
            raise ValueError("Invalid paper or edition in snapshot.")
    return {"snapshot_id": data["snapshot_id"], "generated_at": data.get("generated_at"),
            "source": data["source"], "editions": len(editions), "benchmarks": len(ids), "paper_observations": len(data["papers"])}


def file_sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


# ---------------------------------------------------------------- import

def _fill(con: sqlite3.Connection, data: dict) -> None:
    top = {k: v for k, v in data.items() if k not in ("editions", "benchmarks", "papers")}
    con.executemany("INSERT INTO meta VALUES (?, ?)",
                    [(k, json.dumps(v, ensure_ascii=False)) for k, v in top.items()]
                    + [("db_schema_version", json.dumps(DB_SCHEMA_VERSION))])
    for ed, cov in data["editions"].items():
        venue, year = ed.rsplit("-", 1)
        con.execute("INSERT INTO editions VALUES (?,?,?,?,?,?,?,?)",
                    (ed, venue, int(year), *[cov.get(c) for c in _EDITION_COLUMNS], _residual(cov, _EDITION_COLUMNS)))
    for e in data["benchmarks"]:
        known = ("id", "ok", *_BENCH_COLUMNS, *_BENCH_CHILDREN)
        con.execute("INSERT INTO benchmarks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (e["id"], *[e.get(c) for c in _BENCH_COLUMNS], _j(e.get("ok")),
                     json.dumps(list(e.keys())), _residual(e, known)))
        bid = e["id"]
        con.executemany("INSERT INTO benchmark_aliases VALUES (?,?,?)", [(bid, i, a) for i, a in enumerate(e.get("aliases") or [])])
        con.executemany("INSERT INTO benchmark_merged_ids VALUES (?,?,?)", [(bid, i, a) for i, a in enumerate(e.get("merged_ids") or [])])
        con.executemany("INSERT INTO benchmark_homonyms VALUES (?,?,?)", [(bid, i, a) for i, a in enumerate(e.get("homonym_of") or [])])
        con.executemany("INSERT INTO benchmark_developers VALUES (?,?,?)", [(bid, i, a) for i, a in enumerate(e.get("developers") or [])])
        con.executemany("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?)",
                        [(bid, i, *[c.get(k) for k in _CLAIM_COLUMNS], _residual(c, _CLAIM_COLUMNS))
                         for i, c in enumerate(e.get("claims") or [])])
    obs_id = use_id = unatt_id = 0
    for ord_, p in enumerate(data["papers"]):
        obs_id += 1
        con.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?)",
                    (obs_id, ord_, p["paper_id"], p["edition"], p.get("title"), json.dumps(list(p.keys())),
                     _residual(p, ("paper_id", "edition", "title", "authors", "fields", "uses", "unattributed"))))
        con.executemany("INSERT INTO observation_authors VALUES (?,?,?)", [(obs_id, i, a) for i, a in enumerate(p.get("authors") or [])])
        con.executemany("INSERT INTO observation_fields VALUES (?,?,?,?)",
                        [(obs_id, i, f, f.casefold()) for i, f in enumerate(p.get("fields") or [])])
        for i, (bid, use) in enumerate(p.get("uses", {}).items()):
            use_id += 1
            con.execute("INSERT INTO uses VALUES (?,?,?,?,?,?)",
                        (use_id, obs_id, i, bid, json.dumps(list(use.keys())), _residual(use, ("surfaces", "roles"))))
            con.executemany("INSERT INTO use_surfaces VALUES (?,?,?,?)",
                            [(use_id, j, s, n) for j, (s, n) in enumerate((use.get("surfaces") or {}).items())])
            for j, (role, evs) in enumerate((use.get("roles") or {}).items()):
                con.execute("INSERT INTO use_roles VALUES (?,?,?,?)", (use_id, j, role, len(evs)))
                con.executemany("INSERT INTO evidence (use_id, role, ord, place, text, extra) VALUES (?,?,?,?,?,?)",
                                [(use_id, role, k, ev.get("where"), ev.get("text"), _residual(ev, ("where", "text")))
                                 for k, ev in enumerate(evs)])
        for i, u in enumerate(p.get("unattributed") or []):
            unatt_id += 1
            con.execute("INSERT INTO unattributed VALUES (?,?,?,?,?,?,?,?,?)",
                        (unatt_id, obs_id, i, *[u.get(k) for k in _UNATTRIBUTED_COLUMNS],
                         json.dumps(list(u.keys())), _residual(u, (*_UNATTRIBUTED_COLUMNS, "candidates"))))
            con.executemany("INSERT INTO unattributed_candidates VALUES (?,?,?)",
                            [(unatt_id, j, c) for j, c in enumerate(u.get("candidates") or [])])


def import_snapshot(source: Path, target: Path) -> dict:
    """Build `target` from the JSON snapshot. Builds beside the target and
    replaces it in one step only after the export reads back identical, so a
    failed or interrupted import leaves whatever was there untouched."""
    data = json.loads(source.read_text(encoding="utf-8"))
    validate_snapshot(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, pending = tempfile.mkstemp(prefix=".import-", suffix=".sqlite", dir=target.parent)
    os.close(fd)
    pending = Path(pending)
    try:
        pending.unlink()  # sqlite wants to create the file itself
        con = sqlite3.connect(pending)
        try:
            con.execute("PRAGMA journal_mode=OFF")
            con.execute("PRAGMA synchronous=OFF")
            con.execute("PRAGMA foreign_keys=ON")
            con.executescript(SCHEMA)
            con.execute("BEGIN")
            _fill(con, data)
            con.execute("INSERT INTO meta VALUES (?, ?)", ("source_sha256", json.dumps(file_sha256(source))))
            con.execute("COMMIT")
            con.executescript(INDEXES)
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Import produced dangling references")
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Import produced a corrupt database")
            con.execute("VACUUM")
        finally:
            con.close()
        reread = export_snapshot(pending)
        if reread != data:
            raise ValueError("Imported database does not read back as the source snapshot; nothing was replaced")
        os.replace(pending, target)
    finally:
        if pending.exists():
            pending.unlink()
    checksum = file_sha256(target)
    target.with_name(target.name + ".sha256").write_text(f"{checksum}  {target.name}\n", encoding="utf-8")
    return {"path": str(target), "sha256": checksum, "db_schema_version": DB_SCHEMA_VERSION,
            "snapshot_id": data["snapshot_id"], **counts(target)}


def counts(path: Path) -> dict:
    con = _connect(path)
    try:
        out = {}
        for table, name in (("editions", "editions"), ("benchmarks", "benchmarks"), ("observations", "paper_observations"),
                            ("uses", "uses"), ("evidence", "evidence"), ("unattributed", "unattributed"), ("claims", "claims")):
            out[name] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return out
    finally:
        con.close()


# ---------------------------------------------------------------- export

def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _benchmark_rows(con: sqlite3.Connection) -> list[dict]:
    children = {name: collections.defaultdict(list) for name in _BENCH_CHILDREN}
    for table, name, col in (("benchmark_aliases", "aliases", "alias"), ("benchmark_merged_ids", "merged_ids", "old_id"),
                             ("benchmark_homonyms", "homonym_of", "other_id"), ("benchmark_developers", "developers", "name")):
        for r in con.execute(f"SELECT benchmark_id, {col} AS v FROM {table} ORDER BY benchmark_id, ord"):
            children[name][r["benchmark_id"]].append(r["v"])
    for r in con.execute("SELECT * FROM claims ORDER BY benchmark_id, ord"):
        c = {k: r[k] for k in _CLAIM_COLUMNS if r[k] is not None}
        if r["extra"]:
            c.update(json.loads(r["extra"]))
        children["claims"][r["benchmark_id"]].append(c)
    out = []
    for r in con.execute("SELECT * FROM benchmarks ORDER BY rowid"):
        extra = json.loads(r["extra"]) if r["extra"] else {}
        e = {}
        for key in json.loads(r["keys"]):
            if key == "id":
                e[key] = r["id"]
            elif key == "ok":
                e[key] = json.loads(r["ok"]) if r["ok"] is not None else None
            elif key in _BENCH_COLUMNS:
                e[key] = r[key]
            elif key in _BENCH_CHILDREN:
                rows = children[key].get(r["id"])
                e[key] = rows if rows else (None if key == "homonym_of" else [])
            else:
                e[key] = extra[key]
        out.append(e)
    return out


def _editions(con: sqlite3.Connection) -> dict:
    out = {}
    for r in con.execute("SELECT * FROM editions ORDER BY rowid"):
        cov = {c: r[c] for c in _EDITION_COLUMNS if r[c] is not None}
        if r["extra"]:
            cov.update(json.loads(r["extra"]))
        out[r["edition"]] = cov
    return out


def _meta(con: sqlite3.Connection) -> dict:
    return {r["key"]: json.loads(r["value"]) for r in con.execute("SELECT key, value FROM meta")}


def export_snapshot(path: Path) -> dict:
    """The snapshot dict this database was imported from, exactly."""
    con = _connect(path)
    try:
        meta = _meta(con)
        data = {k: v for k, v in meta.items() if k not in ("db_schema_version", "source_sha256")}
        data["editions"] = _editions(con)
        data["benchmarks"] = _benchmark_rows(con)
        repo = SqliteRepository(path, con=con)
        data["papers"] = repo.observations(None, authors=True, evidence=True)
        return data
    finally:
        con.close()


# ---------------------------------------------------------------- contract

class JsonRepository:
    """The reference: the snapshot dict as loaded."""
    backend = "json"

    def __init__(self, data: dict, available: bool = True):
        self.d = data
        self.available = available
        self._fields = None

    def meta(self) -> dict:
        return {"available": self.available, "backend": self.backend,
                **{k: self.d.get(k) for k in ("source", "snapshot_id", "generated_at", "registry_checked")},
                **({"reason": self.d["reason"]} if self.d.get("reason") else {})}

    def editions(self) -> dict:
        return self.d.get("editions") or {}

    def benchmarks(self) -> list[dict]:
        return self.d["benchmarks"]

    def field_counts(self) -> collections.Counter:
        return collections.Counter(f for p in self.d.get("papers", []) for f in {s.casefold() for s in p.get("fields", [])})

    def field_labels(self) -> set[str]:
        if self._fields is None:
            self._fields = {f.casefold() for p in self.d.get("papers", []) for f in p.get("fields", [])}
        return self._fields

    def observations(self, editions, topic=None, paper_ids=None, *, authors=False, evidence=False) -> list[dict]:
        papers = self.d.get("papers", [])
        if editions is not None:
            eds = set(editions)
            papers = [p for p in papers if p["edition"] in eds]
        if topic:
            t = topic.casefold()
            papers = [p for p in papers if t in {f.casefold() for f in p.get("fields", [])}]
        if paper_ids is not None:
            selected = set(paper_ids)
            papers = [p for p in papers if p["paper_id"] in selected]
        return papers

    def using_papers(self, benchmark_ids) -> list[dict]:
        wanted = set(benchmark_ids)
        return [p for p in self.d.get("papers", [])
                if wanted & set(p["uses"]) or any(wanted & set(u.get("candidates", [])) for u in p.get("unattributed", []))]

    def evidence(self, paper: dict, benchmark_id: str, role: str) -> list[dict]:
        return paper["uses"][benchmark_id]["roles"][role]

    def verify_quote(self, paper_id: str, q: str) -> dict | None:
        for p in self.d.get("papers", []):
            if p["paper_id"] == paper_id:
                for use in p["uses"].values():
                    for evidence in use["roles"].values():
                        if any(not ev.get("where", "").endswith(":cell") and q in " ".join(ev["text"].split()) for ev in evidence):
                            return {"title": p["title"], "edition": p["edition"]}
        for e in self.d["benchmarks"]:
            for c in e.get("claims", []):
                if c["arxiv_base"] == paper_id and q in " ".join(c["evidence"].split()):
                    return {"title": c["title"], "edition": c["edition"]}
        return None


class SqliteRepository:
    """Same contract over the imported file.

    Reads happen inside a pinned session: one read-only connection and one
    read transaction, opened by `BenchmarkStore` around each tool call, so a
    database replaced on disk mid-call cannot mix two versions in one answer
    (the open file keeps the version it opened). Cached lists (catalogue,
    editions, labels) are keyed by the source identity read inside that
    session and dropped when a new file turns up."""
    backend = "sqlite"
    available = True

    def __init__(self, path: Path, con: sqlite3.Connection | None = None):
        self.path = path
        self._local = threading.local()   # one connection per thread, per session
        self._local.con = con
        self._identity = None
        self._meta = None
        self._benchmarks = None
        self._editions = None
        self._fields = None

    @contextlib.contextmanager
    def session(self):
        """Pin every read inside to one version of the file."""
        if getattr(self._local, "con", None) is not None:
            yield self
            return
        try:
            con = _connect(self.path)
            con.execute("BEGIN")
            self._local.con = con
            try:
                m = _meta(con)
                identity = (m.get("snapshot_id"), m.get("source_sha256"), m.get("db_schema_version"))
                if identity != self._identity:
                    self._identity, self._meta, self._benchmarks, self._editions, self._fields = identity, None, None, None, None
                yield self
            finally:
                self._local.con = None
                con.close()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"SQLite database is unreadable ({exc}); rebuild with bench2agent data sqlite") from None

    def _con(self):
        con = getattr(self._local, "con", None)
        if con is None:
            raise RuntimeError("SqliteRepository is read inside a session: use BenchmarkStore or repo.session()")
        return con

    def _done(self, con):
        pass

    def identity(self) -> dict:
        """What this file holds, read once per session: enough to compare
        with the JSON snapshot beside it without loading that snapshot."""
        m = _meta(self._con())
        return {k: m.get(k) for k in ("snapshot_id", "source_sha256", "db_schema_version", "source")}

    def meta(self) -> dict:
        if self._meta is None:
            m = _meta(self._con())
            self._meta = {"available": True, "backend": self.backend,
                          **{k: m.get(k) for k in ("source", "snapshot_id", "generated_at", "registry_checked")},
                          "db_schema_version": m.get("db_schema_version")}
        return self._meta

    def editions(self) -> dict:
        if self._editions is None:
            con = self._con()
            try:
                self._editions = _editions(con)
            finally:
                self._done(con)
        return self._editions

    def benchmarks(self) -> list[dict]:
        if self._benchmarks is None:
            con = self._con()
            try:
                self._benchmarks = _benchmark_rows(con)
            finally:
                self._done(con)
        return self._benchmarks

    def field_counts(self) -> collections.Counter:
        con = self._con()
        try:
            return collections.Counter({r[0]: r[1] for r in con.execute(
                "SELECT label_cf, COUNT(DISTINCT obs_id) FROM observation_fields GROUP BY label_cf")})
        finally:
            self._done(con)

    def field_labels(self) -> set[str]:
        if self._fields is None:
            con = self._con()
            try:
                self._fields = {r[0] for r in con.execute("SELECT DISTINCT label_cf FROM observation_fields")}
            finally:
                self._done(con)
        return self._fields

    def observations(self, editions, topic=None, paper_ids=None, *, authors=False, evidence=False) -> list[dict]:
        con = self._con()
        try:
            con.execute("CREATE TEMP TABLE IF NOT EXISTS sel (obs_id INTEGER PRIMARY KEY)")
            con.execute("DELETE FROM sel")
            where, args = [], []
            if editions is not None:
                con.execute("CREATE TEMP TABLE IF NOT EXISTS sel_ed (edition TEXT PRIMARY KEY)")
                con.execute("DELETE FROM sel_ed")
                con.executemany("INSERT OR IGNORE INTO sel_ed VALUES (?)", [(e,) for e in editions])
                where.append("o.edition IN (SELECT edition FROM sel_ed)")
            if topic:
                where.append("EXISTS (SELECT 1 FROM observation_fields f WHERE f.obs_id = o.obs_id AND f.label_cf = ?)")
                args.append(topic.casefold())
            if paper_ids is not None:
                con.execute("CREATE TEMP TABLE IF NOT EXISTS sel_pid (paper_id TEXT PRIMARY KEY)")
                con.execute("DELETE FROM sel_pid")
                con.executemany("INSERT OR IGNORE INTO sel_pid VALUES (?)", [(p,) for p in paper_ids])
                where.append("o.paper_id IN (SELECT paper_id FROM sel_pid)")
            con.execute("INSERT INTO sel SELECT o.obs_id FROM observations o"
                        + (" WHERE " + " AND ".join(where) if where else ""), args)
            return self._assemble(con, authors=authors, evidence=evidence)
        finally:
            self._done(con)

    def using_papers(self, benchmark_ids) -> list[dict]:
        con = self._con()
        try:
            con.execute("CREATE TEMP TABLE IF NOT EXISTS sel (obs_id INTEGER PRIMARY KEY)")
            con.execute("DELETE FROM sel")
            con.execute("CREATE TEMP TABLE IF NOT EXISTS sel_bid (benchmark_id TEXT PRIMARY KEY)")
            con.execute("DELETE FROM sel_bid")
            con.executemany("INSERT OR IGNORE INTO sel_bid VALUES (?)", [(b,) for b in benchmark_ids])
            con.execute("INSERT OR IGNORE INTO sel SELECT obs_id FROM uses WHERE benchmark_id IN (SELECT benchmark_id FROM sel_bid)")
            con.execute("INSERT OR IGNORE INTO sel SELECT u.obs_id FROM unattributed u JOIN unattributed_candidates c "
                        "ON c.unattributed_id = u.unattributed_id WHERE c.benchmark_id IN (SELECT benchmark_id FROM sel_bid)")
            return self._assemble(con, authors=True, evidence=False)
        finally:
            self._done(con)

    def _assemble(self, con, *, authors: bool, evidence: bool) -> list[dict]:
        papers, by_obs, shapes = [], {}, []
        for r in con.execute("SELECT o.* FROM observations o JOIN sel USING (obs_id) ORDER BY o.ord"):
            p = {"paper_id": r["paper_id"], "edition": r["edition"], "title": r["title"],
                 "authors": [], "fields": [], "uses": {}, "unattributed": []}
            if r["extra"]:
                p.update(json.loads(r["extra"]))
            papers.append(p)
            by_obs[r["obs_id"]] = p
            shapes.append((p, json.loads(r["keys"])))
        if not papers:
            return papers
        if authors:
            for r in con.execute("SELECT a.obs_id, a.name FROM observation_authors a JOIN sel USING (obs_id) ORDER BY a.obs_id, a.ord"):
                by_obs[r[0]]["authors"].append(r[1])
        for r in con.execute("SELECT f.obs_id, f.label FROM observation_fields f JOIN sel USING (obs_id) ORDER BY f.obs_id, f.ord"):
            by_obs[r[0]]["fields"].append(r[1])
        uses = {}
        for r in con.execute("SELECT u.use_id, u.obs_id, u.benchmark_id, u.keys, u.extra FROM uses u JOIN sel USING (obs_id) "
                             "ORDER BY u.obs_id, u.ord"):
            use = {"surfaces": {}, "roles": {}}
            if r[4]:
                use.update(json.loads(r[4]))
            by_obs[r[1]]["uses"][r[2]] = use
            uses[r[0]] = use
            shapes.append((use, json.loads(r[3])))
        for r in con.execute("SELECT s.use_id, s.surface, s.n FROM use_surfaces s JOIN uses u USING (use_id) JOIN sel USING (obs_id) "
                             "ORDER BY s.use_id, s.ord"):
            uses[r[0]]["surfaces"][r[1]] = r[2]
        if evidence:
            for r in con.execute("SELECT r.use_id, r.role FROM use_roles r JOIN uses u USING (use_id) JOIN sel USING (obs_id) "
                                 "ORDER BY r.use_id, r.ord"):
                uses[r[0]]["roles"][r[1]] = []
            for r in con.execute("SELECT e.use_id, e.role, e.place, e.text, e.extra FROM evidence e JOIN uses u USING (use_id) "
                                 "JOIN sel USING (obs_id) ORDER BY e.use_id, e.role, e.ord"):
                ev = {k: v for k, v in (("where", r[2]), ("text", r[3])) if v is not None}
                if r[4]:
                    ev.update(json.loads(r[4]))
                uses[r[0]]["roles"][r[1]].append(ev)
        else:
            # the count stands in for the list: truthy exactly when evidence exists
            for r in con.execute("SELECT r.use_id, r.role, r.n_evidence FROM use_roles r JOIN uses u USING (use_id) "
                                 "JOIN sel USING (obs_id) ORDER BY r.use_id, r.ord"):
                uses[r[0]]["roles"][r[1]] = r[2]
        unatt = {}
        for r in con.execute("SELECT n.* FROM unattributed n JOIN sel USING (obs_id) ORDER BY n.obs_id, n.ord"):
            extra = json.loads(r["extra"]) if r["extra"] else {}
            u = {}
            for key in json.loads(r["keys"]):
                if key in _UNATTRIBUTED_COLUMNS:
                    u[key] = r[key]
                elif key == "candidates":
                    u[key] = []
                else:
                    u[key] = extra[key]
            by_obs[r["obs_id"]]["unattributed"].append(u)
            unatt[r["unattributed_id"]] = u
        for r in con.execute("SELECT c.unattributed_id, c.benchmark_id FROM unattributed_candidates c "
                             "JOIN unattributed n USING (unattributed_id) JOIN sel USING (obs_id) ORDER BY c.unattributed_id, c.ord"):
            unatt[r[0]].setdefault("candidates", []).append(r[1])
        for obj, keys in shapes:
            # a key the source never wrote (a hand-written snapshot without
            # authors, or a use without surfaces) is not written back either
            for key in [k for k in obj if k not in keys]:
                del obj[key]
        return papers

    def evidence(self, paper: dict, benchmark_id: str, role: str) -> list[dict]:
        con = self._con()
        try:
            rows = con.execute(
                "SELECT e.place, e.text, e.extra FROM evidence e JOIN uses u USING (use_id) JOIN observations o USING (obs_id) "
                "WHERE o.paper_id = ? AND o.edition = ? AND u.benchmark_id = ? AND e.role = ? ORDER BY e.ord",
                (paper["paper_id"], paper["edition"], benchmark_id, role)).fetchall()
        finally:
            self._done(con)
        out = []
        for place, text, extra in rows:
            ev = {k: v for k, v in (("where", place), ("text", text)) if v is not None}
            if extra:
                ev.update(json.loads(extra))
            out.append(ev)
        return out

    def verify_quote(self, paper_id: str, q: str) -> dict | None:
        con = self._con()
        try:
            for r in con.execute(
                    "SELECT o.title, o.edition, e.place, e.text FROM evidence e JOIN uses u USING (use_id) "
                    "JOIN observations o USING (obs_id) WHERE o.paper_id = ? ORDER BY o.ord, e.use_id, e.role, e.ord", (paper_id,)):
                if not (r[2] or "").endswith(":cell") and q in " ".join((r[3] or "").split()):
                    return {"title": r[0], "edition": r[1]}
            for r in con.execute("SELECT title, edition, evidence FROM claims WHERE arxiv_base = ? ORDER BY benchmark_id, ord", (paper_id,)):
                if q in " ".join((r[2] or "").split()):
                    return {"title": r[0], "edition": r[1]}
        finally:
            self._done(con)
        return None
