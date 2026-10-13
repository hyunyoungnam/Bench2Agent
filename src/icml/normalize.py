"""Stage 3 — join the feed snapshot with scraped abstracts into the canonical dataset.

Output: data/processed/papers.jsonl — one record per paper, the ONLY file
downstream stages (metrics, labeling, report) are allowed to read.

    python3 -m icml.normalize
"""
from __future__ import annotations

import argparse
import re

from .common import (
    FOCUS_YEAR, PROCESSED, RAW, ROOT, dump_json, ensure_dirs, load_json, read_jsonl, write_jsonl,
)
from .corpus import VENUES, Corpus
from .sources import latest_snapshot

ABSTRACTS = RAW / "abstracts" / "abstracts.jsonl"
OUT = PROCESSED / "papers.jsonl"
# The virtual sites run the same infrastructure, so one normaliser covers every
# venue; icml.sources writes the other venues' lists in the same feed shape.
VENUE_DOMAIN = {"ICML": "https://icml.cc", "NeurIPS": "https://neurips.cc",
                "ICLR": "https://iclr.cc", "CVPR": "https://cvpr.thecvf.com",
                "ICCV": "https://iccv.thecvf.com", "ECCV": "https://eccv.ecva.net"}
VENUE_ARG = VENUES

# Track identification, derived from the feed's sourceurl field.
TRACK_PATTERNS = [
    (re.compile(r"/\d{4}/Conference", re.I), "main"),
    (re.compile(r"thecvf\.com/(CVPR|ICCV|ECCV)|^ECCV\d{4}$|^(cvf|aaai|anthology|pmlr):", re.I), "main"),
    (re.compile(r"Position_Paper_Track", re.I), "position"),
    (re.compile(r"^TMLR", re.I), "tmlr-journal"),
    (re.compile(r"JmlrOrg", re.I), "jmlr-journal"),
    (re.compile(r"ANN-STATS", re.I), "annals-stats"),
]

# Light-touch institution cleanup: the feed's free-text field is inconsistent.
_INST_TRIM = re.compile(r"\s*,?\s*(inc\.?|ltd\.?|llc)\s*$", re.I)


def classify_track(sourceurl: str | None) -> str:
    s = sourceurl or ""
    for pattern, name in TRACK_PATTERNS:
        if pattern.search(s):
            return name
    return "other"


_ALIAS_FILE = ROOT / "config" / "institution_aliases.json"
_ALIASES: dict[str, str] | None = None


def _aliases() -> dict[str, str]:
    global _ALIASES
    if _ALIASES is None:
        _ALIASES = load_json(_ALIAS_FILE)["aliases"] if _ALIAS_FILE.exists() else {}
    return _ALIASES


def normalize_institution(raw: str | None) -> str | None:
    """Free-text affiliation -> a canonical-ish name.

    Deliberately conservative: collapse whitespace, drop a repeated
    "X, X" self-reference, then apply the explicit alias table. Anything not in
    the table is left exactly as the author wrote it — inventing fuzzy matches
    here would silently merge distinct labs.
    """
    if not raw:
        return None
    s = re.sub(r"\s+", " ", raw).strip().strip(",")
    s = _INST_TRIM.sub("", s)
    if not s:
        return None

    # "Tsinghua University, Tsinghua University" -> "Tsinghua University"
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) > 1 and len(set(p.lower() for p in parts)) == 1:
        s = parts[0]

    return _aliases().get(s.lower(), s)


# Top-level primary areas of the OpenReview submission forms (ICML, ICLR,
# NeurIPS). NeurIPS 2023 wrote "Area/Subarea" with a slash; only a slash
# after one of these names is a separator ("Health / Medicine" and
# "Batch/Offline" are subarea names, never split).
PRIMARY_AREAS = {"Applications", "Computer Vision", "Data-centric AI", "Deep Learning", "General Machine Learning",
                 "Miscellaneous Aspects of Machine Learning", "Optimization", "Probabilistic Methods",
                 "Reinforcement Learning", "Social Aspects", "Theory"}


def split_topic(topic: str | None) -> tuple[str | None, str | None]:
    """'Deep Learning->Attention Mechanisms' -> ('Deep Learning', 'Attention Mechanisms');
    NeurIPS 2023's 'Applications/Computer Vision' the same way."""
    if not topic:
        return None, None
    parts = [p.strip() for p in topic.split("->") if p.strip()]
    if not parts:
        return None, None
    if len(parts) == 1 and parts[0].count("/") == 1 and " / " not in parts[0]:
        left, right = (x.strip() for x in parts[0].split("/"))
        if left in PRIMARY_AREAS and right:
            return left, right
    return parts[0], (parts[1] if len(parts) > 1 else None)


def topic_origin(sourceurl: str | None, topic: str | None) -> str | None:
    """Where a row's `topic` comes from. `declared`: the primary area the
    authors chose on the submission form (virtual-site feeds, OpenReview);
    `venue_track`: the venue's own track the paper was published in (AAAI
    OJS sections); `venue_volume`: the proceedings volume (ACL Anthology
    ids such as 2021.acl-long), which is publication information, not a
    field. None when the row carries no topic."""
    if not topic:
        return None
    s = sourceurl or ""
    if s.startswith("aaai:"):
        return "venue_track"
    if s.startswith("anthology:"):
        return "venue_volume"
    return "declared"


def _person(name: str) -> str:
    import unicodedata
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", n).strip()


def openreview_id(paper_url: str | None) -> str | None:
    if not paper_url:
        return None
    m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", paper_url)
    return m.group(1) if m else None


def dedupe_key(rec: dict) -> str:
    """The normalized title: the clue that proposes identity candidates
    (`identify`), never the key that merges rows."""
    return "title:" + re.sub(r"\W+", "", (rec.get("name") or "").lower())


_SYNTHETIC_FORUM = re.compile(r"^\d{4}-(?:oral|spotlight|poster)--", re.I)


def official_id(rec: dict) -> tuple[str, str] | None:
    """The paper's own identifier in its source, with its kind: an OpenReview
    forum id (the synthetic per-event ids the virtual sites give an Oral
    listing, "2025-Oral--4871-…", are not one), an Anthology / PMLR / OJS
    record id, or a proceedings URL. None when the row carries none."""
    oid = openreview_id(rec.get("paper_url"))
    if oid and not _SYNTHETIC_FORUM.match(oid):
        return ("openreview", oid)
    su = rec.get("sourceurl") or ""
    for prefix in ("anthology:", "pmlr:", "aaai:"):
        if su.startswith(prefix):
            return (prefix[:-1], f"{su}#{rec['id']}")
    pu = rec.get("paper_url") or ""
    if pu.startswith("http") and "openreview.net" not in pu:
        return ("url", pu)
    return None


def track_of(sourceurl: str | None) -> str | None:
    """The feed track a row belongs to, normalized: the satellite listings
    ("cdmx-…-cdmx" at NeurIPS 2025) name the same track; "None" or an empty
    value is unknown (an Oral row at ECCV 2024) and matches any track."""
    s = (sourceurl or "").strip()
    if not s or s == "None":
        return None
    return re.sub(r"^cdmx-|-cdmx$", "", s)


def feed_keys(rec: dict) -> tuple[str | None, str | None]:
    """The feed's two schedule keys, (|sourceid|, uid), each None when the row
    lacks it. The virtual sites give an Oral row the Poster row's `sourceid`
    negated and the same `uid`; a feed link needs BOTH to agree (every one of
    the 61 feeds carries both keys, so a missing key is a feed change or a
    collection fault, recorded and never bridged by the other key)."""
    sid = rec.get("sourceid")
    try:
        sid = str(abs(int(sid))) if sid not in (None, "", 0) else None
    except (TypeError, ValueError):
        sid = None
    uid = str(rec["uid"]) if rec.get("uid") else None
    return sid, uid


def identify(rows: list[dict]) -> list[list[dict]]:
    """Group schedule rows into papers. Rows merge only on an official id or
    on a feed link (same |sourceid| within the track AND the same uid), and
    only when the two groups being joined are compatible as wholes: no two
    disjoint author lists, and no two different official ids of one kind. A
    key whose rows are incompatible, or feed keys that disagree (one
    matches, the other differs), merge nothing and are recorded on every
    row involved (`identity_conflict`); a row missing one feed key is
    recorded (`identity_missing_key`) and links by its official id only.
    Titles never merge: rows left apart that share a title are recorded on
    each other as `identity_candidates`. Rows are processed in a fixed
    order, so the result does not depend on input order."""
    order = sorted(range(len(rows)), key=lambda i: (str(rows[i].get("eventtype") or ""), str(rows[i]["event_id"])))
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def names(i):
        return {_person(a) for a in rows[i].get("authors") or [] if _person(a)}

    def members(root):
        return [i for i in order if find(i) == root]

    def incompatible(group) -> str | None:
        """Why a set of rows cannot be one paper, or None."""
        sets = [names(i) for i in group]
        if any(a and b and not (a & b) for x, a in enumerate(sets) for b in sets[x + 1:]):
            return "disjoint author lists"
        ids: dict[str, set[str]] = {}
        for i in group:
            key = official_id(rows[i]["_rec"])
            if key:
                ids.setdefault(key[0], set()).add(key[1])
        for kind, values in ids.items():
            if len(values) > 1:
                return f"different {kind} ids"
        return None

    def conflict(group, key, reason):
        for i in group:
            rows[i].setdefault("_conflicts", []).append({"key": key[0], "value": key[1], "reason": reason,
                                                         "rows": sorted(rows[j]["event_id"] for j in group)})

    def link(members_, key):
        """Join rows on one key, checking the whole groups they belong to."""
        if len(members_) < 2:
            return
        if (reason := incompatible(members_)):
            conflict(members_, key, reason)
            return
        for i in members_[1:]:
            a, b = find(members_[0]), find(i)
            if a == b:
                continue
            whole = members(a) + members(b)
            if (reason := incompatible(whole)):
                conflict(whole, key, reason + " (through an earlier merge)")
                continue
            parent[b] = a
        for i in members_:
            rows[i]["_identity"].append(key[0])

    for i in order:
        rows[i]["_identity"] = []
    # 1. official ids
    keyed: dict[tuple[str, str], list[int]] = {}
    for i in order:
        key = official_id(rows[i]["_rec"])
        if key:
            keyed.setdefault(key, []).append(i)
    for key, members_ in keyed.items():
        link(members_, key)
    # 2. feed links: |sourceid| within the track and uid must both agree
    by_sid: dict[str, list[int]] = {}
    for i in order:
        sid, uid = feed_keys(rows[i]["_rec"])
        if sid is None or uid is None:
            if sid is not None or uid is not None:
                rows[i]["_missing_key"] = "uid" if uid is None else "sourceid"
            continue
        by_sid.setdefault(sid, []).append(i)
    for sid, members_ in by_sid.items():
        if len(members_) < 2:
            continue
        by_track: dict[str | None, list[int]] = {}
        for i in members_:
            by_track.setdefault(track_of(rows[i]["_rec"].get("sourceurl")), []).append(i)
        unknown = by_track.pop(None, [])
        if len(by_track) == 1:
            track, tracked = next(iter(by_track.items()))
            scoped = {track: tracked + unknown}
        elif not by_track:
            scoped = {"*": unknown}
        else:
            scoped = by_track               # an unknown-track row stays alone when the id is used in several tracks
        for track, group in scoped.items():
            if len(group) < 2:
                continue
            by_uid: dict[str, list[int]] = {}
            for i in group:
                by_uid.setdefault(feed_keys(rows[i]["_rec"])[1], []).append(i)
            if len(by_uid) > 1:
                conflict(group, ("feed", f"{track}|{sid}"), "feed keys disagree: one |sourceid|, several uids")
                continue
            link(group, ("feed", f"{track}|{sid}"))
    # the same uid with different |sourceid| values is the mirror disagreement
    by_uid_all: dict[str, list[int]] = {}
    for i in order:
        sid, uid = feed_keys(rows[i]["_rec"])
        if sid is not None and uid is not None:
            by_uid_all.setdefault(f"{track_of(rows[i]['_rec'].get('sourceurl'))}|{uid}", []).append(i)
    for key, group in by_uid_all.items():
        if len(group) > 1 and len({feed_keys(rows[i]["_rec"])[0] for i in group}) > 1:
            conflict(group, ("feed", key), "feed keys disagree: one uid, several |sourceid|")
    groups: dict[int, list[dict]] = {}
    for i in order:
        groups.setdefault(find(i), []).append(rows[i])
    # same title, not merged: candidates, never a merge
    by_title: dict[str, list[int]] = {}
    for root, g in groups.items():
        for r in g:
            by_title.setdefault(r["_key"], []).append(root)
    for title_key, roots in by_title.items():
        distinct = sorted(set(roots))
        if len(distinct) > 1:
            for root in distinct:
                others = [groups[o][0]["event_id"] for o in distinct if o != root]
                for r in groups[root]:
                    r["_candidates"] = [{"reason": "same title", "event_ids": others}]
    return [groups[root] for root in sorted(groups, key=lambda root: (str(groups[root][0].get("eventtype") or ""), str(groups[root][0]["event_id"])))]


def merge_duplicates(group: list[dict]) -> dict:
    """Collapse one paper's schedule entries into a single record.

    Oral status is the union (a paper with an Oral slot IS an oral), and we keep
    the entry that carries an abstract so nothing is lost.
    """
    best = sorted(group, key=lambda r: (r.get("abstract") is None, not r.get("is_oral")))[0]
    merged = dict(best)

    # Coalesce, don't pick a record wholesale. The Oral schedule entry usually
    # carries no `topic`, so choosing it discarded the Poster entry's
    # area/subarea — measured: 96 papers, and orals are exactly what people
    # browse first. Take each field from whichever sibling actually has it.
    for field in ("area", "subarea", "topic_raw", "topic_origin", "decision", "abstract",
                  "paper_url", "openreview_id", "virtual_url"):
        if not merged.get(field):
            for r in group:
                if r.get(field):
                    merged[field] = r[field]
                    break

    merged["is_oral"] = any(r.get("is_oral") for r in group)
    merged["is_spotlight"] = any(r.get("is_spotlight") for r in group)
    if merged["is_oral"]:
        merged["presentation"] = "Oral"
    merged["duplicate_event_ids"] = sorted(
        r["event_id"] for r in group if r["event_id"] != best["event_id"])
    # every source identifier the merged rows carried, so the raw rows stay
    # traceable: the identity rule is title plus author overlap, not the title
    merged["source_ids"] = sorted({str(sid) for r in group for sid in r.get("_source_ids", [])})
    merged["titles"] = sorted({r["title"] for r in group if r.get("title")})
    merged["schedule_rows"] = [{"event_id": r["event_id"], "presentation": r.get("presentation"), "title": r.get("title"),
                                "official_id": (official_id(r["_rec"]) or [None, None])[1]}
                               for r in sorted(group, key=lambda r: str(r["event_id"]))]
    evidence = sorted({e for r in group for e in r.get("_identity", [])})
    merged["identity_rule"] = "merged by " + "+".join(evidence) if len(group) > 1 else "single row"
    if any(r.get("_conflicts") for r in group):
        seen_conf, conflicts = set(), []
        for c in (c for r in group for c in r.get("_conflicts", [])):
            mark = (c["key"], c["value"], c["reason"])
            if mark not in seen_conf:
                seen_conf.add(mark)
                conflicts.append(c)
        merged["identity_conflict"] = conflicts
    if any(r.get("_missing_key") for r in group):
        merged["identity_missing_key"] = sorted({r["_missing_key"] for r in group if r.get("_missing_key")})
    if any(r.get("_candidates") for r in group):
        merged["identity_candidates"] = [c for r in group for c in r.get("_candidates", [])]
    return merged


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the canonical papers dataset.")
    ap.add_argument("--allow-missing-abstracts", action="store_true",
                    help="build even if abstract coverage is below the 90%% threshold")
    ap.add_argument("--year", type=int, default=FOCUS_YEAR)
    ap.add_argument("--venue", default="icml", choices=sorted(VENUE_ARG))
    args = ap.parse_args()
    venue = VENUE_ARG[args.venue]
    corpus = Corpus(venue, args.year)
    out = corpus.papers

    ensure_dirs()
    snap = latest_snapshot(args.venue, args.year)
    if snap is None:
        raise SystemExit("no feed snapshot — run `python3 -m icml.sources collect` first")

    # Abstracts, best source first: the feed itself carries them for past
    # editions (NeurIPS 2025, ICLR 2025 — 100%); a current edition needs the
    # scraped file (abstracts.jsonl for ICML, abstracts_<venue>_<year>.jsonl
    # otherwise, from `icml.sources abstracts`).
    if venue == "ICML":
        abs_path = ABSTRACTS
    else:
        abs_path = RAW / f"abstracts_{args.venue}_{args.year}.jsonl"
    abstracts = {}
    if abs_path.exists():
        abstracts = {
            (row.get("event_id") or row["id"]): row["abstract"]
            for row in read_jsonl(abs_path)
            if row.get("abstract")
        }
    results = load_json(snap)["results"]

    rows = []
    # a retired virtual site marks every row invisible (ICLR 2020); the flag
    # then says nothing about the paper, so it is ignored when no row is visible
    any_visible = any(rec.get("visible", True) for rec in results)
    if results and not any_visible:
        print(f"note: no row is marked visible in this feed; keeping all {len(results)} rows")
    for rec in results:
        if any_visible and not rec.get("visible", True):
            continue
        area, subarea = split_topic(rec.get("topic"))
        authors = rec.get("authors") or []
        institutions = []
        for a in authors:
            inst = normalize_institution(a.get("institution"))
            if inst and inst not in institutions:
                institutions.append(inst)

        decision = rec.get("decision") or ""
        rows.append({
            "_key": dedupe_key(rec),
            "_rec": rec,
            "_source_ids": [x for x in (rec.get("sourceid"), openreview_id(rec.get("paper_url")),
                                        rec.get("paper_url"), f"{rec.get('sourceurl')}#{rec['id']}") if x],
            "venue": venue,
            "year": args.year,
            "event_id": rec["id"],
            "uid": rec.get("uid"),
            "title": (rec.get("name") or "").strip(),
            "abstract": abstracts.get(rec["id"]) or (rec.get("abstract") or "").strip() or None,
            "authors": [a.get("fullname") for a in authors if a.get("fullname")],
            "author_institutions": [
                {"name": a.get("fullname"), "institution": normalize_institution(a.get("institution"))}
                for a in authors
            ],
            "institutions": institutions,
            "n_authors": len(authors),
            "area": area,
            "subarea": subarea,
            "topic_raw": rec.get("topic") or None,
            "topic_origin": topic_origin(rec.get("sourceurl"), rec.get("topic")),
            "presentation": rec.get("eventtype"),
            "decision": decision or None,
            "is_spotlight": "spotlight" in decision.lower(),
            "is_oral": (rec.get("eventtype") or "").lower() == "oral",
            "track": classify_track(rec.get("sourceurl")),
            "openreview_id": openreview_id(rec.get("paper_url")),
            "paper_url": rec.get("paper_url"),
            "virtual_url": (VENUE_DOMAIN[venue] + rec["virtualsite_url"])
                           if rec.get("virtualsite_url") and venue in VENUE_DOMAIN else None,
        })

    # Collapse the oral/poster double-listing before anything is counted:
    # official ids and the feed's own schedule links merge; titles only
    # propose candidates (two teams under one title stay two papers).
    raw_rows = len(rows)
    parts = identify(rows)
    rows = [merge_duplicates(part) for part in parts]
    conflicts = sum(1 for r in rows if r.get("identity_conflict"))
    candidates = sum(1 for r in rows if r.get("identity_candidates"))
    missing = sum(1 for r in rows if r.get("identity_missing_key"))
    if conflicts or candidates or missing:
        print(f"note: {candidates} paper(s) share a title with another paper without an id link (kept apart); "
              f"{conflicts} with an id conflict (not merged); {missing} with a missing feed key")
    for row in rows:
        for k in ("_key", "_source_ids", "_rec", "_identity", "_conflicts", "_candidates", "_missing_key"):
            row.pop(k, None)
    rows.sort(key=lambda r: r["event_id"])
    collapsed = raw_rows - len(rows)
    if collapsed:
        print(f"deduplicated {collapsed} duplicate schedule entries "
              f"({raw_rows} rows -> {len(rows)} papers)")

    with_abs = sum(1 for r in rows if r["abstract"])
    coverage = with_abs / len(rows) if rows else 0.0

    if coverage < 0.90 and not args.allow_missing_abstracts:
        raise SystemExit(
            f"abstract coverage {coverage:.1%} ({with_abs}/{len(rows)}) is below 90%.\n"
            "Finish `python3 -m icml.abstracts` first, or pass --allow-missing-abstracts "
            "to build a partial dataset (reports must then state the coverage)."
        )

    n = write_jsonl(out, rows)
    if corpus.is_focus:              # the manifest describes the canonical corpus only
        dump_json(PROCESSED / "dataset_manifest.json", {
            "source_snapshot": snap.name,
            "papers": n,
            "abstract_coverage": round(coverage, 4),
            "papers_with_abstract": with_abs,
        })
    print(f"wrote {out.relative_to(out.parents[2])}: {n} papers, abstract coverage {coverage:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
