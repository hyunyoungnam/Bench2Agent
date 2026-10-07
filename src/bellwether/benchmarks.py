"""Portable full-text benchmark statistics and evidence; standard library only.

Build a snapshot AFTER the mention pass and reviewed identities have settled.
Every parsed paper is retained, including papers with no stated role. Runtime
queries never substitute the abstract inventory for missing full-text data.
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import math
import os
import re
import unicodedata
from pathlib import Path

from .paths import ROOT
SNAPSHOT = "benchmark_snapshot.json"
ROLES = ("evaluates_on", "trains_on")
# Where a registered benchmark lives is OUR mapping (config/benchmarks.json):
# a Hub dataset id or a GitHub repo, shown only when it answered the API at
# the registry's last check, or an author-registered homepage, never checked.
HOSTS = (("hf", "huggingface", "https://huggingface.co/datasets/"),
         ("gh", "github", "https://github.com/"))
LINK_NOTE = ("locations: our mapping from this benchmark id to where the artifact lives. "
             "check=api: the Hub/GitHub id answered the API when the registry was last checked "
             "(checked_at null means the date is not recorded in this snapshot); check=none: an "
             "author-registered homepage. An empty list is no confirmed location, not none. "
             "introducing_paper: the reviewed introducing paper in this corpus, null when there is "
             "none; it is a paper, not a download location. Neither ranks or filters anything.")


def locations(entry: dict, checked_at: str | None = None) -> list[dict]:
    ok = entry.get("ok") or {}
    out = [{"url": prefix + entry[field], "host": host, "check": "api", "checked_at": checked_at}
           for field, host, prefix in HOSTS if entry.get(field) and ok.get(field)]
    if entry.get("url"):
        out.append({"url": entry["url"], "host": "web", "check": "none", "checked_at": None})
    return out


def introducing_paper(entry: dict) -> dict | None:
    claims = entry.get("claims") or []
    if not claims:
        return None
    c = next((c for c in claims if c["edition"] == entry.get("first_claim")), claims[0])
    return {"paper_id": c["arxiv_base"], "url": "https://arxiv.org/abs/" + c["arxiv_base"],
            "edition": c["edition"], "title": c["title"]}


def fold(s: str) -> str:
    s = s.lower().translate(str.maketrans({"τ": "tau", "∞": "inf", "²": "2", "³": "3"}))
    s = re.sub(r"^(the|a)\s+", "", s)
    s = re.sub(r"\s+(benchmark|dataset|corpus|suite)s?$", "", s)
    return re.sub(r"[^a-z0-9+]", "", s)


def person(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", s).strip()


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def rows(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def catalogue(root: Path) -> list[dict]:
    reg = read_json(root / "config/benchmarks.json", {"benchmarks": []})
    out = {e["id"]: dict(e) for e in reg["benchmarks"]}
    intro = read_json(root / "config/benchmarks_introduced.json", {})
    # Only the reviewed schema is eligible: old title-gated outputs are not a
    # substitute for claim-by-claim review.
    for e in intro.get("benchmarks", []):
        if "developers" in e and e.get("claims"):
            out[e["id"]] = {**out.get(e["id"], {}), **e}
    # Two extraction claims in one introducing paper can name the same
    # product. They are duplicates, not homonyms (e.g. LIBERO's expanded name).
    # Preserve all claim sentences and remap IDs only in the serving snapshot.
    parent = {bid: bid for bid in out}

    def find(bid):
        while parent[bid] != bid:
            parent[bid] = parent[parent[bid]]
            bid = parent[bid]
        return bid

    known = {}
    for e in out.values():
        for c in e.get("claims", []):
            key = (fold(e["name"]), e.get("kind"), c["arxiv_base"])
            if key in known:
                parent[find(e["id"])] = find(known[key])
            known[key] = e["id"]
    groups = collections.defaultdict(list)
    for e in out.values():
        groups[find(e["id"])].append(e)
    merged, remap = [], {}
    for group in groups.values():
        group.sort(key=lambda e: (not bool(e.get("registered")), len(e["id"]), e["id"]))
        entry = dict(group[0])
        ids = {e["id"] for e in group}
        for bid in ids:
            remap[bid] = entry["id"]
        if len(group) > 1:
            entry["merged_ids"] = sorted(ids - {entry["id"]})
            claims = {c.get("claim_id", c["arxiv_base"] + c["evidence"]): c
                      for e in group for c in e.get("claims", [])}
            entry["claims"] = sorted(claims.values(), key=lambda c: (c["edition"].rsplit("-", 1)[1], c["edition"], c["arxiv_base"]))
            entry["first_claim"] = entry["claims"][0]["edition"]
            entry["developers"] = sorted({a for e in group for a in e.get("developers", [])})
            entry["aliases"] = sorted({a for e in group for a in e.get("aliases", [])})
            entry["homonym_of"] = sorted({bid for e in group for bid in e.get("homonym_of") or []} - ids) or None
        merged.append(entry)
    for e in merged:
        if e.get("homonym_of"):
            e["homonym_of"] = sorted({remap.get(bid, bid) for bid in e["homonym_of"]} - {e["id"]}) or None
    out = {e["id"]: e for e in merged}
    return list(out.values())


def _files(root: Path, edition: str) -> tuple[Path, Path]:
    venue, year = edition.rsplit("-", 1)
    stem = "papers" if edition == "icml-2026" else (
        f"papers_{year}" if venue == "icml" else f"papers_{venue}_{year}")
    resolved = "resolved" if edition == "icml-2026" else f"resolved_{edition}"
    return root / "data/processed" / (stem + ".jsonl"), root / "data/raw/arxiv" / (resolved + ".jsonl")


def _cited(title: str, refs: list[str]) -> bool:
    # Matching titles, not names, distinguishes homonyms. No global reference
    # list is used: refs must come from the sentence asserting THIS role.
    key = fold(title)
    return len(key) >= 12 and any(key in fold(r) for r in refs)


def build_snapshot(root: Path = ROOT, out: Path | None = None) -> dict:
    files = sorted((root / "data/interim/mentions").glob("mentions_*.jsonl"))
    if not files:
        raise FileNotFoundError("No full-text mention outputs. Finish icml.bench_mentions --all first.")
    dependencies = files + [root / "config/benchmarks.json", root / "config/benchmarks_introduced.json"]
    stamps = {}

    def remember(paths):
        for p in paths:
            if p.exists():
                stamps[str(p.relative_to(root))] = (p.stat().st_mtime_ns, p.stat().st_size)

    remember(dependencies)
    metadata = {}
    topics = {}
    editions = {}
    for file in files:
        ed = file.stem.removeprefix("mentions_")
        if not re.fullmatch(r"[a-z]+-\d{4}", ed):
            raise ValueError(f"Invalid edition: {ed}")
        papers_file, resolved = _files(root, ed)
        if not papers_file.exists() or not resolved.exists():
            raise FileNotFoundError(f"Paper metadata and arXiv mapping required for {ed}")
        dependencies += [papers_file, resolved]
        remember([papers_file, resolved])
        papers = {p["event_id"]: p for p in rows(papers_file)}
        tp = root / f"data/processed/topics_{ed}.json"
        if tp.exists():
            dependencies.append(tp)
            remember([tp])
        for t in read_json(tp, {}).get("topics", []):
            for eid in set(t.get("explicit", []) + t.get("via_child", [])):
                topics.setdefault((ed, eid), []).append(t["label"])
        matched = set()
        for r in rows(resolved):
            base = r.get("arxiv_base")
            p = papers.get(r.get("event_id"))
            if base and p:
                matched.add(base)
                metadata[(ed, base)] = {"title": p["title"], "authors": p.get("authors") or [],
                    "fields": sorted(set(topics.get((ed, p["event_id"]), []) +
                                         [s for s in (p.get("area"), p.get("subarea")) if s]))}
        editions[ed] = {"total_papers": len(papers), "arxiv_matched": len(matched)}
    # Read source stamps before the expensive scan; reject a changing source
    # rather than publishing a mixture of two extraction revisions.
    entries = catalogue(root)
    by_key = collections.defaultdict(dict)
    by_fold = collections.defaultdict(dict)
    for e in entries:
        for name in [e["name"], *e.get("aliases", [])]:
            by_fold[fold(name)][e["id"]] = e
        key = e.get("registered") or e.get("homonym_of_registered") or (
            "hom:" + e.get("fold", fold(e["name"])) if e.get("homonym_of") else e["id"])
        by_key[key][e["id"]] = e
        for old_id in e.get("merged_ids", []):
            by_key[old_id][e["id"]] = e
    # Registered products and their homonyms must both be candidates even if
    # only one has an introducing paper inside the observation window.
    for key, candidates in by_key.items():
        for e in list(candidates.values()):
            for other in by_fold[fold(e["name"])].values():
                candidates[other["id"]] = other
    result = []
    for file in files:
        ed = file.stem.removeprefix("mentions_")
        seen = set()
        for r in rows(file):
            base = r["arxiv_base"]
            if base in seen:
                raise ValueError(f"Duplicate paper {ed}/{base}")
            seen.add(base)
            meta = metadata.get((ed, base))
            if meta is None:
                raise ValueError(f"Missing paper metadata: {ed}/{base}")
            uses, unresolved = {}, []
            for key, m in r.get("mentions", {}).items():
                candidates = list(by_key.get(key, {}).values())
                if not candidates:
                    # Unreviewed provisional vocabulary is evidence of a
                    # name, not an established entity to rank.
                    if m.get("roles"):
                        unresolved.append({"key": key, "name": m.get("as_written"), "reason": "unreviewed"})
                    continue
                for role, evidence in m.get("roles", {}).items():
                    if role not in ROLES:
                        continue
                    selected = candidates
                    if len(candidates) > 1:
                        refs = m.get("role_cites", [])
                        if isinstance(refs, dict):
                            refs = refs.get(role, [])
                        selected = [e for e in candidates if any(_cited(c["title"], refs)
                                                               for c in e.get("claims", []))]
                        if len(selected) != 1:
                            unresolved.append({"key": key, "role": role, "reason": "homonym",
                                               "candidates": [e["id"] for e in candidates]})
                            continue
                    e = selected[0]
                    use = uses.setdefault(e["id"], {"surfaces": {}, "roles": {}})
                    use["surfaces"].update(m.get("surfaces") or {m.get("as_written", e["name"]): 1})
                    use["roles"].setdefault(role, []).extend(evidence)
            result.append({"paper_id": base, "edition": ed, **meta, "uses": uses,
                           "unattributed": unresolved})
        editions[ed]["parsed_papers"] = len(seen)
        editions[ed]["field_labelled_papers"] = sum(bool(p["fields"]) for p in result if p["edition"] == ed)
    for name, stamp in stamps.items():
        p = root / name
        if (p.stat().st_mtime_ns, p.stat().st_size) != stamp:
            raise ValueError(f"Source changed during export: {name}; retry after extraction finishes")
    source_id = hashlib.sha256(json.dumps(stamps, sort_keys=True).encode()).hexdigest()[:16]
    data = {"schema_version": 1, "snapshot_id": source_id,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source": "arxiv_html_stated_roles", "editions": editions,
            # the registry's own API-check date, never the export date
            "registry_checked": read_json(root / "config/benchmarks.json", {}).get("checked"),
            "benchmarks": entries, "papers": result}
    target = out or root / "data/processed" / SNAPSHOT
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, target)
    return {"path": str(target), "snapshot_id": source_id, "editions": len(editions),
            "parsed_papers": len(result), "benchmarks": len(entries)}


class BenchmarkStore:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self._stamp = None
        self._data = None

    def data(self) -> dict:
        path = self.root / "data/processed" / SNAPSHOT
        if not path.exists():
            return {"source": "arxiv_html_stated_roles", "available": False,
                    "benchmarks": catalogue(self.root), "papers": [], "editions": {}}
        stamp = (path.stat().st_mtime_ns, path.stat().st_size)
        if stamp != self._stamp:
            data = read_json(path)
            if data.get("schema_version") != 1 or data.get("source") != "arxiv_html_stated_roles":
                raise ValueError("Unsupported benchmark snapshot; rebuild with bellwether benchmarks")
            self._data, self._stamp = {**data, "available": True}, stamp
        return self._data

    def _base(self, data: dict) -> dict:
        return {k: data.get(k) for k in ("available", "source", "snapshot_id", "generated_at")}

    def _links(self, data: dict, entry: dict) -> dict:
        """Keyed by the entry (benchmark id), never by name: a homonym gets its own."""
        return {"locations": locations(entry, data.get("registry_checked")),
                "introducing_paper": introducing_paper(entry)}

    def _entry(self, data: dict, name: str) -> dict:
        exact = [e for e in data["benchmarks"] if e["id"] == name or name in e.get("merged_ids", [])]
        found = exact or [e for e in data["benchmarks"]
                          if fold(name) in {fold(s) for s in [e["name"], *e.get("aliases", [])]}]
        if len(found) != 1:
            raise ValueError("Pass a benchmark ID from benchmark_usage or new_benchmarks. "
                             + json.dumps([{"id": e["id"], "name": e["name"]} for e in found]))
        return found[0]

    def scope(self, a: dict) -> dict:
        data = self.data()
        fields = collections.Counter(f for p in data["papers"] for f in {s.casefold() for s in p.get("fields", [])})
        query = (a.get("query") or "").casefold()
        return {**self._base(data), "editions": data["editions"],
                "fields": [{"name": f, "parsed_papers": n} for f, n in fields.most_common()
                           if query in f.casefold()][:100],
                "catalogue_entries": len(data["benchmarks"]),
                "note": "Fields are exact declared topic/area/subarea labels; their coverage is incomplete. "
                        "Usage needs benchmark_snapshot.json; missing data is not zero usage."}

    def _select(self, data: dict, a: dict, *, latest: bool = False) -> tuple[list, dict]:
        eds = list(data["editions"])
        venue = (a.get("venue") or "").lower()
        if venue:
            eds = [ed for ed in eds if ed.rsplit("-", 1)[0] == venue]
        if a.get("edition"):
            eds = [ed for ed in eds if ed == a["edition"].lower()]
        if a.get("years"):
            eds = [ed for ed in eds if int(ed.rsplit("-", 1)[1]) in a["years"]]
        if latest and not a.get("edition") and not a.get("years"):
            newest = {}
            for ed in eds:
                v = ed.rsplit("-", 1)[0]
                newest[v] = max(newest.get(v, ed), ed)
            eds = list(newest.values())
        if data["available"] and not eds:
            raise ValueError("No analysed editions match the requested scope")
        papers = [p for p in data["papers"] if p["edition"] in eds]
        topic = a.get("topic")
        if topic:
            known = {f.casefold() for p in data["papers"] for f in p.get("fields", [])}
            if topic.casefold() not in known:
                raise ValueError("Unknown field label; call benchmark_scope(query=...) or supply paper_ids")
            papers = [p for p in papers if topic.casefold() in {f.casefold() for f in p.get("fields", [])}]
        if a.get("paper_ids") is not None:
            selected = set(a["paper_ids"])
            papers = [p for p in papers if p["paper_id"] in selected]
        denominators = collections.Counter(p["edition"] for p in papers)
        cov = {ed: {**data["editions"][ed], "in_scope_parsed": denominators[ed]} for ed in sorted(eds)}
        return papers, {"topic": topic, "editions": cov,
                        "parsed_papers": len(papers),
                        "field_labelled_papers": sum(bool(p.get("fields")) for p in papers),
                        "unattributed_papers": sum(bool(p.get("unattributed")) for p in papers),
                        "note": "Denominators include every successfully parsed paper in this scope, "
                                "including those with no stated benchmark. Latest editions differ by venue. "
                                "Pooled counts are paper-edition observations. Field filters cover labelled papers only; "
                                "zero in-scope papers is not proof a venue has no papers in that field."}

    def _require(self, data: dict) -> dict | None:
        if not data["available"]:
            return {**self._base(data), "error": "Full-text benchmark usage snapshot is not installed. "
                    "After extraction and identity review, run bellwether benchmarks on the build machine "
                    "and distribute the data bundle. Abstract mentions are not a fallback."}
        return None

    @staticmethod
    def _role(a: dict) -> str:
        role = a.get("role", "evaluates_on")
        if role not in ROLES:
            raise ValueError("role must be evaluates_on or trains_on")
        return role

    @staticmethod
    def _paper(p: dict, bid: str, role: str) -> dict:
        return {"paper_id": p["paper_id"], "title": p["title"], "edition": p["edition"],
                "url": "https://arxiv.org/abs/" + p["paper_id"],
                "surfaces": p["uses"][bid].get("surfaces", {}),
                "evidence": p["uses"][bid]["roles"][role]}

    def usage(self, a: dict) -> dict:
        data = self.data()
        if error := self._require(data):
            return error
        papers, coverage = self._select(data, a, latest=a.get("latest", True))
        role = self._role(a)
        counts = collections.defaultdict(list)
        for p in papers:
            for bid, use in p["uses"].items():
                if use["roles"].get(role):
                    counts[bid].append(p)
        cat = {e["id"]: e for e in data["benchmarks"]}
        limit = max(1, min(int(a.get("limit", 10)), 100))
        results = [{"id": bid, "name": cat[bid]["name"], "kind": cat[bid].get("kind"),
                    "papers": len(ps), "per_1000": round(len(ps) / len(papers) * 1000, 3),
                    **self._links(data, cat[bid]),
                    "evidence": [self._paper(p, bid, role) for p in ps[:2]]}
                   for bid, ps in sorted(counts.items(), key=lambda x: (-len(x[1]), x[0]))[:limit]]
        return {**self._base(data), "role": role, "coverage": coverage, "results": results,
                "total_benchmarks": len(counts), "truncated": len(counts) > limit,
                "note": "Counts measure explicitly stated use, not quality. Missing stated roles do not prove non-use. "
                        + LINK_NOTE}

    def evidence(self, a: dict) -> dict:
        data = self.data()
        if error := self._require(data):
            return error
        entry = self._entry(data, a["benchmark"])
        papers, coverage = self._select(data, a)
        role = self._role(a)
        matches = [self._paper(p, entry["id"], role) for p in papers
                   if p["uses"].get(entry["id"], {}).get("roles", {}).get(role)]
        offset, limit = max(0, int(a.get("offset", 0))), max(1, min(int(a.get("limit", 10)), 100))
        return {**self._base(data), "id": entry["id"], "name": entry["name"], "role": role,
                **self._links(data, entry),
                "coverage": coverage, "total": len(matches), "results": matches[offset:offset + limit],
                "truncated": offset + limit < len(matches), "note": LINK_NOTE}

    def trend(self, a: dict) -> dict:
        data = self.data()
        if error := self._require(data):
            return error
        entry = self._entry(data, a["benchmark"])
        papers, coverage = self._select(data, a)
        role = self._role(a)
        counts = collections.Counter(p["edition"] for p in papers
                    if p["uses"].get(entry["id"], {}).get("roles", {}).get(role))
        results, comparisons = [], []
        for ed, cov in coverage["editions"].items():
            n, k = cov["in_scope_parsed"], counts[ed]
            results.append({"edition": ed, "papers": k, "parsed_papers": n,
                            "per_1000": round(k / n * 1000, 3) if n else None})
        venues = collections.defaultdict(list)
        for r in results:
            venues[r["edition"].rsplit("-", 1)[0]].append(r)
        for series in venues.values():
            for old, new in zip(series, series[1:]):
                n1, n2, k1, k2 = old["parsed_papers"], new["parsed_papers"], old["papers"], new["papers"]
                z, state = None, "insufficient_scope"
                if n1 and n2:
                    pooled = (k1 + k2) / (n1 + n2)
                    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
                    z = (k2 / n2 - k1 / n1) / se if se else 0.0
                    state = "no_detected_change"
                    if k1 == 0 and k2 >= 8:
                        state = "appearing"
                    elif abs(z) >= 2.576:
                        state = "rising" if z > 0 else "falling"
                comparisons.append({"from": old["edition"], "to": new["edition"],
                                    "z": round(z, 3) if z is not None else None, "state": state})
        return {**self._base(data), "id": entry["id"], "name": entry["name"], "role": role,
                **self._links(data, entry),
                "coverage": coverage, "results": results, "comparisons": comparisons,
                "rule": "Within venue: two-proportion z, abs(z) >= 2.576; 0 to >=8 papers is appearing."}

    def adoption(self, a: dict) -> dict:
        data = self.data()
        if error := self._require(data):
            return error
        entry = self._entry(data, a["benchmark"])
        if not entry.get("claims"):
            return {"error": "No reviewed introducing paper in this corpus; developer attribution is unavailable."}
        papers, coverage = self._select(data, a)
        return {**self._base(data), **self._adoption(entry, papers), **self._links(data, entry),
                "coverage": coverage}

    def _adoption(self, entry: dict, papers: list) -> dict:
        claimers = {c["arxiv_base"] for c in entry["claims"]}
        devs = {person(s) for s in entry.get("developers", []) if person(s)}
        first_year = int(entry["first_claim"].rsplit("-", 1)[1])
        totals = {role: {who: 0 for who in ("others", "self", "undetermined")} for role in ROLES}
        before, per_edition = [], {}
        grouped = collections.defaultdict(list)
        for p in sorted(papers, key=lambda p: (p["edition"].rsplit("-", 1)[1], p["edition"])):
            if p["uses"].get(entry["id"]) and p["paper_id"] not in claimers:
                grouped[p["paper_id"]].append(p)
        ambiguous = {p["paper_id"] for p in papers if p["paper_id"] not in claimers and any(
            entry["id"] in u.get("candidates", []) for u in p.get("unattributed", []))}
        for observed in grouped.values():
            p = observed[0]
            if int(p["edition"].rsplit("-", 1)[1]) < first_year:
                before.append(p["paper_id"])
                continue
            users = {person(s) for obs in observed for s in obs.get("authors", []) if person(s)}
            who = "undetermined" if not users or not devs else "self" if users & devs else "others"
            ec = per_edition.setdefault(p["edition"], {r: {w: 0 for w in totals[r]} for r in ROLES})
            for role in ROLES:
                if any(obs["uses"][entry["id"]]["roles"].get(role) for obs in observed):
                    totals[role][who] += 1
                    ec[role][who] += 1
        return {"id": entry["id"], "name": entry["name"],
                "first_claim": entry["first_claim"], **totals,
                "by_edition": per_edition, "before_claim": len(before),
                "unattributed_using_papers": len(ambiguous),
                "note": "Unique using papers, introducing papers excluded. Same-name attribution requires local "
                        "citation evidence; unattributed uses are additional unresolved observations, not non-use. "
                        "Duplicate paper observations are merged and assigned to their earliest observed edition. "
                        "Author-name matching is conservative; same-year ordering is unknown. "
                        "first_claim means first reviewed claim in this corpus, not first public release."}

    def new(self, a: dict) -> dict:
        data = self.data()
        kind = a.get("kind", "benchmark")
        if kind not in ("benchmark", "dataset"):
            raise ValueError("kind must be benchmark or dataset")
        sort = a.get("sort", "introduced")
        if sort not in ("introduced", "adoption"):
            raise ValueError("sort must be introduced or adoption")
        entries = [e for e in data["benchmarks"] if e.get("claims") and e.get("kind") == kind]
        if a.get("edition"):
            entries = [e for e in entries if e["first_claim"] == a["edition"].lower()]
        if a.get("venue"):
            entries = [e for e in entries if e["first_claim"].rsplit("-", 1)[0] == a["venue"].lower()]
        if a.get("years"):
            entries = [e for e in entries if int(e["first_claim"].rsplit("-", 1)[1]) in a["years"]]
        if a.get("topic") or a.get("paper_ids") is not None:
            if error := self._require(data):
                return error
            selected, _ = self._select(data, {k: v for k, v in a.items() if k in ("topic", "paper_ids")})
            ids = {p["paper_id"] for p in selected}
            entries = [e for e in entries if any(c["arxiv_base"] in ids for c in e["claims"])]
        entries.sort(key=lambda e: (-int(e["first_claim"].rsplit("-", 1)[1]), e["id"]))
        stats = {}
        if a.get("include_adoption") or sort == "adoption":
            if error := self._require(data):
                return error
            wanted = {e["id"] for e in entries}
            using = collections.defaultdict(list)
            for p in data["papers"]:
                relevant = set(p["uses"]) & wanted
                relevant |= {bid for u in p.get("unattributed", []) for bid in u.get("candidates", []) if bid in wanted}
                for bid in relevant:
                    using[bid].append(p)
            stats = {e["id"]: self._adoption(e, using[e["id"]]) for e in entries}
            role = self._role({"role": a.get("adoption_role", "trains_on" if kind == "dataset" else "evaluates_on")})
            if sort == "adoption":
                entries.sort(key=lambda e: (-stats[e["id"]][role]["others"], e["id"]))
        offset, limit = max(0, int(a.get("offset", 0))), max(1, min(int(a.get("limit", 10)), 100))
        return {**self._base(data), "total": len(entries), "kind": kind,
                "results": [{**{k: e.get(k) for k in ("id", "name", "kind", "first_claim", "claims", "homonym_of")},
                             **self._links(data, e),
                             **({"adoption": stats[e["id"]]} if e["id"] in stats else {})}
                            for e in entries[offset:offset + limit]],
                "truncated": offset + limit < len(entries),
                "sort": sort,
                "adoption_scope": "All installed editions, no field filter; introduction filters do not filter using papers." if stats else None,
                "note": "Reviewed introductions are listed regardless of adoption. first_claim is inside this "
                        "corpus, not global priority. Call benchmark_adoption for use counts; unavailable is not zero. "
                        + LINK_NOTE}

    def verify(self, paper_id: str, quote: str) -> dict | None:
        if len(quote.strip()) < 20:
            return None
        q = " ".join(quote.split())
        data = self.data()
        for p in data["papers"]:
            if p["paper_id"] == paper_id:
                for use in p["uses"].values():
                    for evidence in use["roles"].values():
                        if any(not ev.get("where", "").endswith(":cell") and
                               q in " ".join(ev["text"].split()) for ev in evidence):
                            return {"title": p["title"], "edition": p["edition"]}
        for e in data["benchmarks"]:
            for c in e.get("claims", []):
                if c["arxiv_base"] == paper_id and q in " ".join(c["evidence"].split()):
                    return {"title": c["title"], "edition": c["edition"]}
        return None
