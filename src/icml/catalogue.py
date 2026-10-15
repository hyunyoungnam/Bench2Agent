"""Catalogue, review and adoption over the mention outputs: the reviewed
introduction claims (`introduced`), their review file, retained and stale
reviews, and adoption counts. Moved out of `bench_mentions` on 2026-10-15 so
that a change here is not a change of the extraction rules: `icml.update`
hashes `bench_mentions.py` (extraction) apart from this module.

    python3 -m icml.bench_mentions --introduced | --adoption | --summary | --review-export | --review-import
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from .common import ROOT, dump_json, load_json, read_jsonl
from .taxonomy import dataset_key
from .bench_mentions import HTML_DIR, _slug, _strong_name, INTRODUCED, OUT_DIR, REGISTRY, _INTRO_NOT_NAME, _INTRO_REF, _SENT, _plain_word


def paper_index() -> dict[str, dict]:
    """arxiv_base -> {edition, year, title, authors} over every collected edition."""
    from .corpus import EDITIONS, Corpus
    idx: dict[str, dict] = {}
    for disp, year in EDITIONS:
        c = Corpus(disp, year)
        res = ROOT / "data" / "raw" / "arxiv" / ("resolved.jsonl" if c.is_focus else f"resolved_{c.key}.jsonl")
        if not (res.exists() and c.papers.exists()):
            continue
        by_event = {p["event_id"]: p for p in read_jsonl(c.papers)}
        for r in read_jsonl(res):
            p = by_event.get(r["event_id"])
            if r.get("arxiv_base") and p:
                idx.setdefault(r["arxiv_base"], {"edition": c.key, "year": year, "title": p["title"],
                                                 "authors": p.get("authors") or []})
    return idx






def _title_names(name: str, title: str) -> bool:
    """Does the paper's title name it as its subject? Either the title leads
    with it ("MMMU: A Massive ...") or it is a product-shaped name of 5+
    characters anywhere in the title ("Judging LLM-as-a-Judge with MT-Bench").
    Used to ORDER the review, never to decide it (Codex review, 2026-10-06)."""
    if not re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", title, re.I):
        return False
    if re.match(rf"\s*{re.escape(name)}\s*(?::|—|–|-\s|\band\b)", title, re.I):
        return True
    return _strong_name(name) and len(dataset_key(name)) >= 5


# ---------------------------------------------------------------- claims
# The unit of review is one claim: one paper saying it built one name. The
# decision lives in config/introduced_review.json (written by Claude Code's
# review, never by a rule), so regenerating the claims never loses it.
REVIEW = ROOT / "config" / "introduced_review.json"
LABELS = {
    "E": ("confirmed", "introduces a benchmark it evaluates on"),
    "T": ("confirmed", "introduces a dataset or resource for training or other use"),
    "U": ("excluded", "uses or rebuilds existing data; introduces nothing"),
    "X": ("excluded", "extraction error: a real claim, but the name is wrong"),
    "N": ("excluded", "not data: a method, model, metric or task"),
    "?": ("undetermined", "the sentence and its context do not settle it"),
}


def _load_review() -> dict:
    return load_json(REVIEW) if REVIEW.exists() else {"claims": {}, "ids": {}}


def all_claims() -> list[dict]:
    """Every introduction claim in the mention outputs, one row per (paper, name)."""
    idx = paper_index()
    reg = load_json(REGISTRY)["benchmarks"]
    reg_fold = {dataset_key(s_): e["id"] for e in reg for s_ in [e["name"], *e.get("aliases", [])]}
    out = []
    for f in sorted(OUT_DIR.glob("mentions_*.jsonl")):
        ed = f.stem.removeprefix("mentions_")
        for r in read_jsonl(f):
            for it in (r.get("introduces") or {}).values():
                fold = dataset_key(it["name"])
                if fold in _INTRO_NOT_NAME or _INTRO_REF.match(it["name"]):
                    continue
                p = idx.get(r["arxiv_base"], {})
                out.append({"claim_id": f"{r['arxiv_base']}#{fold}", "name": it["name"], "fold": fold,
                            "noun": it["noun"], "edition": ed, "year": p.get("year"),
                            "arxiv_base": r["arxiv_base"], "title": p.get("title") or "",
                            "authors": p.get("authors") or [], "registered": reg_fold.get(fold),
                            "in_title": _title_names(it["name"], p.get("title") or ""),
                            "evidence": it["evidence"][0]["text"]})
    return out


def _context(claims: list[dict]) -> dict[str, str]:
    """claim_id -> the claim sentence with the sentences around it, from the parsed text."""
    want: dict[str, list[dict]] = collections.defaultdict(list)
    for c in claims:
        want[c["arxiv_base"]].append(c)
    out = {}
    for f in sorted(HTML_DIR.glob("fulltext_*.jsonl")):
        for row in read_jsonl(f):
            for c in want.get(row.get("arxiv_base"), []):
                for sec in row.get("sections") or []:
                    sents = _SENT.split(sec["text"])
                    hit = next((i for i, s_ in enumerate(sents) if c["evidence"][:120] in s_), None)
                    if hit is not None:
                        out[c["claim_id"]] = " ".join(sents[max(hit - 1, 0): hit + 2])[:900]
                        break
    return out


def review_export(path: str, sample: int = 0, seed: int = 0) -> None:
    """Unreviewed claims as a TSV to read: n, claim_id, name, noun, edition,
    in_title, registered, title, context. `sample` draws a stratified sample
    (in-title, body-only, homonym, plain-word, registered) instead of all."""
    import random
    done = _load_review()["claims"]
    claims = [c for c in all_claims() if c["claim_id"] not in done]
    if sample:
        by_fold = collections.Counter(c["fold"] for c in claims)
        strata = {"in_title": [c for c in claims if c["in_title"] and not c["registered"]],
                  "body_only": [c for c in claims if not c["in_title"] and not c["registered"]],
                  "homonym": [c for c in claims if by_fold[c["fold"]] > 1],
                  "plain_word": [c for c in claims if _plain_word(c["name"]) or
                                 (" " in c["name"] and not _strong_name(c["name"]))],
                  "registered": [c for c in claims if c["registered"]]}
        rnd, picked, seen = random.Random(seed), [], set()
        for name, pool in strata.items():
            for c in rnd.sample(pool, min(len(pool), sample // len(strata))):
                if c["claim_id"] not in seen:
                    seen.add(c["claim_id"])
                    picked.append({**c, "stratum": name})
        claims = picked
    else:
        claims.sort(key=lambda c: (not c["in_title"], c["fold"], c["year"] or 0))
    ctx = _context(claims)
    with open(path, "w", encoding="utf-8") as fh:
        for n, c in enumerate(claims):
            text = re.sub(r"\s+", " ", ctx.get(c["claim_id"], c["evidence"]))
            fh.write("\t".join([str(n), c["claim_id"], c["name"], c["noun"], c["edition"],
                                "T" if c["in_title"] else "-", c["registered"] or "-",
                                c.get("stratum", ""), c["title"][:160], text]) + "\n")
    print(f"review: {len(claims)} claims -> {path}")


def review_import(path: str) -> None:
    """Lines 'claim_id<TAB>label[<TAB>note]' -> config/introduced_review.json."""
    rev = _load_review()
    current = {c["claim_id"]: hashlib.sha256(c["evidence"].encode()).hexdigest()[:16] for c in all_claims()}
    n = 0
    for line in open(path, encoding="utf-8"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 2 or parts[1] not in LABELS:
            continue
        rev["claims"][parts[0]] = {"label": parts[1], "status": LABELS[parts[1]][0],
                                   **({"note": parts[2]} if len(parts) > 2 and parts[2] else {}),
                                   "reviewed": dt.date.today().isoformat(),
                                   # the sentence this decision was made on; a changed sentence makes the decision stale
                                   **({"evidence_sha": current[parts[0]]} if parts[0] in current else {})}
        n += 1
    dump_json(REVIEW, rev)
    print(f"review: {n} decisions recorded, {len(rev['claims'])} in total -> {REVIEW.name}")


def retained_claim(cid: str, kept: dict, paper: dict) -> dict:
    """A vanished reviewed claim as the catalogue lists it: the sentence,
    title and authors kept on the review entry, the paper index only where
    it still knows the paper (`year`); the kept title and authors stand
    when the paper has left the matched set."""
    return {"claim_id": cid, "name": kept.get("name"), "fold": dataset_key(kept.get("name") or ""),
            "noun": kept.get("noun"), "edition": kept.get("edition"), "year": paper.get("year"),
            "arxiv_base": kept.get("arxiv_base"), "title": kept.get("title") or paper.get("title") or "",
            "authors": list(paper.get("authors") or kept.get("authors") or []), "registered": None, "in_title": False,
            "evidence": kept.get("evidence") or "", "vanished": True}


def review_for(claim: dict, entry: dict | None) -> dict | None:
    """The review that applies to a claim as extracted NOW. A review made on
    another sentence (`evidence_sha` differs) is stale: none of it — label,
    name correction, target — is reused, and the claim is pending. A claim
    that vanished from the latest text keeps its review until a reviewer
    decides `retain` or `retire`, and says so."""
    if entry is None:
        return None
    sha = hashlib.sha256(claim["evidence"].encode()).hexdigest()[:16]
    if claim.get("vanished") and entry.get("status") == "confirmed":
        decision = entry.get("vanished_decision") or {}
        if decision.get("decision") == "retire":
            return {**entry, "status": "excluded", "label": "U", "retired": decision.get("reason")}
        if decision.get("decision") == "retain":
            return {**entry, "status": "confirmed", "retained": decision.get("reason")}
        return {**entry, "status": "confirmed", "vanished_in_latest_version": True,
                "pending_decision": "retain or retire (docs/introduced-review.md)"}
    if entry.get("evidence_sha") and entry["evidence_sha"] != sha:
        return {"status": "stale", "label": entry.get("label"), "stale": "evidence changed since review"}
    return entry


def _person(n: str) -> str:
    import unicodedata
    n = unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", n).strip()


def introduced() -> Path:
    """Reviewed claims -> benchmark identities in config/benchmarks_introduced.json.

    A benchmark's id belongs to the benchmark, not to a paper: confirmed claims
    on one name whose papers share an author (arXiv and conference versions, a
    follow-up) are one benchmark; claims with no author in common are different
    products under one name and get separate ids, marked as homonyms. A name
    already in the registry keeps its registry id; its confirmed in-corpus
    claims are recorded against it. Ids persist in introduced_review.json."""
    rev = _load_review()
    ids = rev.setdefault("ids", {})
    reg = load_json(REGISTRY)["benchmarks"]
    reg_ids = {e["id"] for e in reg}
    reg_fold = {dataset_key(s_): e["id"] for e in reg for s_ in [e["name"], *e.get("aliases", [])]}
    claims = all_claims()
    present = {c["claim_id"] for c in claims}
    # a reviewed claim whose sentence is no longer extracted keeps its last
    # sentence (`retained_evidence`, written by icml.update) and its
    # benchmark stays listed, marked, until a reviewer decides `retain`
    # (the claim stands on the old sentence) or `retire` (excluded); other
    # claims of the same benchmark are untouched either way
    idx = paper_index()
    for cid, entry in rev["claims"].items():
        kept = entry.get("retained_evidence")
        if cid in present or not kept or entry.get("status") != "confirmed":
            continue
        claims.append(retained_claim(cid, kept, idx.get(kept.get("arxiv_base"), {})))
    for c in claims:
        c["review"] = review_for(c, rev["claims"].get(c["claim_id"]))
    for c in claims:
        # the review's correction of the name: an X with "real name N; E|T" is a
        # real claim under N, and "name N" on E/T sets the short published name
        note = ((c["review"] or {}).get("note") or "")
        # reviewers wrote "expansion of BEAF" as well as "name BEAF"
        note = re.sub(r"^\s*expansion of (?:the )?(?:published )?(?:short name )?", "name ", note, flags=re.I)
        m = re.match(r"\s*(?:real\s+)?name\s+(?!unknown|none|not\s+stated|unnamed|n/a)([^;(/]+?)\s*"
                     r"(?:\(.*\))?\s*(?:;\s*(?:([ET])\b)?.*)?$", note, re.I)
        if m and re.search(r"\s/\s|,", note.split(";")[0]):
            m = None                        # several names in one claim: not one benchmark
        if c["review"] and m and c["review"]["label"] in ("X", "E", "T") and c["review"].get("status") in ("confirmed", "excluded"):
            c["name"], c["fold"] = m.group(1).strip(), dataset_key(m.group(1))
            if c["review"]["label"] == "X":
                kind = m.group(2) or ("E" if re.search(r"bench|suite|test|evaluat", c["noun"]) else "T")
                c["review"] = {**c["review"], "status": "confirmed", "label": kind, "renamed_from_X": True}
            # renamed onto a registered name: whether this paper is that
            # benchmark's own is a review decision recorded as `target`
            # (benchmark_id, decision, evidence, reviewer); a correction without
            # one waits for review and is counted nowhere. The paper's title is
            # not a rule here (Codex review, 2026-10-13).
            c["registered"] = reg_fold.get(c["fold"])
            if c["registered"]:
                target = (c["review"] or {}).get("target") or {}
                decision = target.get("decision") if target.get("benchmark_id") == c["registered"] and target.get("evidence") else None
                if decision == "same":
                    c["review"] = {**c["review"], "note": "same", "identity_evidence": "reviewed:" + target["evidence"]}
                elif decision == "homonym":
                    c["review"] = {**c["review"], "note": "homonym"}
                elif decision == "excluded":
                    c["review"] = {**c["review"], "status": "excluded", "label": "U"}
                else:
                    c["review"] = {**c["review"], "status": "pending", "pending": "correction onto a registered name needs a "
                                   "reviewed target (benchmark_id, decision, evidence)"}
    confirmed = [c for c in claims if c["review"] and c["review"]["status"] == "confirmed"]
    confirmed.sort(key=lambda c: (c["year"] or 9999, c["edition"]))
    groups: dict[str, list[list[dict]]] = collections.defaultdict(list)
    for c in confirmed:
        # a claim on a registered name is that benchmark unless the review says
        # it is another product with the same name ("MTBench" for motion
        # transfer is not MT-Bench)
        hom = bool(c["registered"]) and (c["review"].get("note") or "").startswith("homonym")
        c["homonym_of_registered"] = c["registered"] if hom else None
        if c["registered"] and not hom:
            groups[c["fold"] + "|reg"] = groups.get(c["fold"] + "|reg") or [[]]
            groups[c["fold"] + "|reg"][0].append(c)
            continue
        # the same paper (arXiv versions, a second claim sentence) is one
        # product; anything else waits for the evidence pass below
        for g in groups[c["fold"]]:
            if c["arxiv_base"] == g[0]["arxiv_base"]:
                g.append(c)
                break
        else:
            groups[c["fold"]].append([c])
    # Two papers claiming one name are one product only by a reviewed
    # `same_product` record (introduced_review.json: {"same_product": [{"fold",
    # "arxiv_bases", "evidence", "reviewed_by", "reviewed"}]}). Shared
    # authors, a citation between them or a shared repository are relations
    # recorded as `identity_candidates`, never a merge (Codex review, 2026-10-13).
    same_product = {(x["fold"], frozenset(x["arxiv_bases"])): x for x in rev.get("same_product", [])}
    for fold_, gs in groups.items():
        if fold_.endswith("|reg") or len(gs) < 2:
            continue
        for record in [x for x in same_product.values() if x["fold"] == fold_]:
            members = [g for g in gs if g[0]["arxiv_base"] in record["arxiv_bases"]]
            if len(members) > 1 and record.get("evidence"):
                head = members[0]
                for g in members[1:]:
                    for c in g:
                        c["identity_evidence"] = "reviewed same_product"
                    head.extend(g)
                    gs.remove(g)
    needed = {c["arxiv_base"] for gs in groups.values() if len(gs) > 1 for g in gs for c in g}
    refs = references_of(needed) if needed else {}
    candidates = collections.defaultdict(list)   # (fold, first paper) -> [{other, relations}]
    for fold_, gs in groups.items():
        if fold_.endswith("|reg") or len(gs) < 2:
            continue
        for i, a in enumerate(gs):
            for b in gs[i + 1:]:
                pa = {_person(x) for c in a for x in c["authors"]}
                pb = {_person(x) for c in b for x in c["authors"]}
                relations = []
                if pa & pb:
                    relations.append("shared author")
                if any(_cites_paper(refs.get(cb["arxiv_base"], []), ca["title"]) for ca in a for cb in b):
                    relations.append("cited by the later paper")
                if any(_cites_paper(refs.get(ca["arxiv_base"], []), cb["title"]) for ca in a for cb in b):
                    relations.append("cites the later paper")
                if relations:
                    candidates[(fold_, a[0]["arxiv_base"])].append({"other": (fold_, b[0]["arxiv_base"]), "relations": relations})
                    candidates[(fold_, b[0]["arxiv_base"])].append({"other": (fold_, a[0]["arxiv_base"]), "relations": relations})
    entities = []
    for gkey, gs in groups.items():
        fold = gkey.split("|")[0]
        for g in gs:
            key = f"{gkey}@{g[0]['arxiv_base']}"
            if gkey.endswith("|reg"):
                ids[key] = g[0]["registered"]          # the registered benchmark's own paper
            elif key not in ids:
                base, k = _slug(g[0]["name"]), 2
                i, taken = base, set(ids.values()) | reg_ids
                while i in taken:
                    i, k = f"{base}-{k}", k + 1
                ids[key] = i
            seen_papers, uniq = set(), []
            for c in g:                     # an X renamed onto a name its paper also claims
                if c["arxiv_base"] not in seen_papers:
                    seen_papers.add(c["arxiv_base"])
                    uniq.append(c)
            g[:] = uniq
            labels = {c["review"]["label"] for c in g}
            entities.append({
                "id": ids[key], "name": g[0]["name"], "fold": fold,
                "registered": g[0]["registered"] if gkey.endswith("|reg") else None,
                "homonym_of_registered": g[0].get("homonym_of_registered"),
                "kind": "benchmark" if "E" in labels else "dataset",
                "homonym_of": None,                         # filled below
                "identity_candidates": None,                # filled below: related claims under this name, never merged
                "_candidates": candidates.get((fold, g[0]["arxiv_base"]), []),
                "identity_evidence": sorted({c.get("identity_evidence") or "same-paper" for c in g}),
                "first_claim": g[0]["edition"],
                "claims": [{k_: c[k_] for k_ in ("claim_id", "edition", "arxiv_base", "title", "noun",
                                                 "evidence")} | {"label": c["review"]["label"]}
                           | ({"vanished_in_latest_version": True, "pending_decision": c["review"].get("pending_decision")}
                              if c["review"].get("vanished_in_latest_version") else {})
                           | ({"retained": c["review"]["retained"]} if c["review"].get("retained") else {}) for c in g],
                "developers": sorted({a for c in g for a in c["authors"]})})
    # homonym ids exist only after every group got one; fill the cross-links
    by_fold = collections.defaultdict(list)
    by_first = {}
    for e in entities:
        by_fold[e["fold"]].append(e["id"])
        by_first[(e["fold"], e["claims"][0]["arxiv_base"])] = e["id"]
    for e in entities:
        e["identity_candidates"] = sorted(({"id": by_first[c["other"]], "relations": c["relations"]} for c in e.pop("_candidates")
                                           if c["other"] in by_first), key=lambda x: x["id"]) or None
        e["homonym_of"] = sorted(i for i in by_fold[e["fold"]] if i != e["id"]) or None
        if e["homonym_of_registered"] and e["homonym_of_registered"] not in (e["homonym_of"] or []):
            e["homonym_of"] = sorted((e["homonym_of"] or []) + [e["homonym_of_registered"]])
    status = collections.Counter((c["review"] or {}).get("status", "candidate") for c in claims)
    print(f"introduced: claim status {dict(status)}")
    dump_json(REVIEW, rev)
    dump_json(INTRODUCED, {
        "_note": ("Benchmarks and datasets a paper in this corpus says it built, one entry per benchmark "
                  "(owner, 2026-10-06; rebuilt after the Codex review). Only claims Claude Code's review "
                  "confirmed are here; candidates, exclusions and undetermined claims are counted in "
                  "`claims` and listed in introduced_review.json. kind 'benchmark' = some claim says it is "
                  "evaluated on; 'dataset' = training data or a resource. `first_claim` is the first edition "
                  "HERE that claims it, never 'first developed': the corpus starts in 2023. Same name, no "
                  "shared author = a different product (`homonym_of`)."),
        "claims": dict(status), "benchmarks": sorted(entities, key=lambda e: e["id"])})
    print(f"introduced: {len(claims)} claims — {dict(status)}; {len(entities)} benchmarks "
          f"({sum(1 for e in entities if e['homonym_of'])} under a shared name) -> {INTRODUCED.name}")
    return INTRODUCED


def references_of(bases: set[str]) -> dict[str, list[str]]:
    """arxiv_base -> reference strings, read from the parsed full text of the
    editions those papers belong to (only those files are opened)."""
    idx = paper_index()
    editions = {idx[b]["edition"] for b in bases if b in idx}
    out: dict[str, list[str]] = {}
    for ed in sorted(editions):
        f = HTML_DIR / f"fulltext_{ed}.jsonl"
        if not f.exists():
            continue
        for row in read_jsonl(f):
            if row.get("arxiv_base") in bases:
                out[row["arxiv_base"]] = [x if isinstance(x, str) else json.dumps(x) for x in (row.get("references") or [])]
    return out


_POSITION = re.compile(r"^\s*position(?:\s+paper)?\s*:\s*", re.I)


def _cites_paper(ref_texts: list[str], title: str) -> bool:
    """Does a reference list name this title? The whole normalized title must
    occur in a reference entry (ICML position papers' "Position:" prefix is
    dropped, as citations omit it). A reference that abbreviates or varies
    the title is not a confirmed citation: it stays unverified."""
    t = dataset_key(_POSITION.sub("", title))
    return len(t) >= 12 and any(t in dataset_key(r) for r in ref_texts)


def adoption() -> Path:
    """For every confirmed benchmark: stated uses in other papers, by role.

    A use is a stated role (evaluates_on / trains_on) — the same evidence every
    count needs. Homonyms add one condition: the role sentences must cite that
    product's introducing paper, or the use stays `unattributed`. Authors:
    'self' = shares an author with a confirmed introducing paper; 'undetermined'
    when either side has no author list; 'others' otherwise. A use in an
    edition earlier than the first claim goes to `before_claim`."""
    intro = load_json(INTRODUCED)["benchmarks"]
    idx = paper_index()
    by_key: dict[str, list[dict]] = collections.defaultdict(list)
    for e in intro:
        key = e["registered"] or e.get("homonym_of_registered") or \
            (("hom:" + e["fold"]) if e["homonym_of"] else e["id"])
        by_key[key].append(e)
    rows = {e["id"]: {"id": e["id"], "name": e["name"], "kind": e["kind"], "first_claim": e["first_claim"],
                      "homonym_of": e["homonym_of"], "before_claim": [], "unattributed": 0,
                      **{r: {"others": 0, "self": 0, "undetermined": 0} for r in ("evaluates_on", "trains_on")}}
            for e in intro}
    for f in sorted(OUT_DIR.glob("mentions_*.jsonl")):
        ed = f.stem.removeprefix("mentions_")
        for r in read_jsonl(f):
            p = idx.get(r["arxiv_base"], {})
            for k, m in r["mentions"].items():
                es = by_key.get(k)
                if not es or not m.get("roles"):
                    continue
                if len(es) > 1 or es[0].get("homonym_of_registered"):
                    # same name, several products: the role sentence's citation
                    # decides; with none, a registered name keeps its own
                    # benchmark, and two new products stay unattributed
                    cited = [e for e in es if any(_cites_paper(m.get("role_cites", []), c["title"])
                                                  for c in e["claims"])]
                    own = [e for e in es if e.get("registered")]
                    if len(cited) == 1:
                        es = cited
                    elif not cited and own:
                        es = own
                    elif not cited and all(e.get("homonym_of_registered") for e in es):
                        continue                       # the registered one, not in this table
                    else:
                        for e in by_key[k]:
                            rows[e["id"]]["unattributed"] += 1
                        continue
                e = es[0]
                if r["arxiv_base"] in {c["arxiv_base"] for c in e["claims"]}:
                    continue                                   # its own introducing paper
                first_year = idx.get(e["claims"][0]["arxiv_base"], {}).get("year", 0)
                if p.get("year", 9999) < first_year:
                    rows[e["id"]]["before_claim"].append(ed)
                    continue
                devs = {_person(a) for a in e["developers"]}
                users = {_person(a) for a in p.get("authors", [])}
                who = "undetermined" if not devs or not users else ("self" if devs & users else "others")
                for role in m["roles"]:
                    rows[e["id"]][role][who] += 1
    out_rows = sorted(rows.values(), key=lambda r: (-r["evaluates_on"]["others"], r["id"]))
    return _strict_pass(out_rows, intro, idx)


STRICT_BEFORE = 3   # uses of a name before its claim edition that mark it as already meaning something


def _strict_pass(rows: list[dict], intro: list[dict], idx: dict) -> Path:
    """A name already in use before its claim ("RGB", "SAM", "AMD") had another
    meaning: recount it so that only role sentences citing the introducing
    paper count, everywhere. Other names keep the stated-role evidence alone."""
    strict = {r["id"] for r in rows if len(r["before_claim"]) >= STRICT_BEFORE}
    # a new name that is the bare front of a registered one ("AMC" of AMC 2023,
    # "Minerva" of Minerva Math): other papers' uses mostly mean the registered one
    reg_folds = [dataset_key(s_) for e in load_json(REGISTRY)["benchmarks"]
                 for s_ in [e["name"], *e.get("aliases", [])]]
    for e in intro:
        if not e["registered"] and any(rf != e["fold"] and rf.startswith(e["fold"]) for rf in reg_folds):
            strict.add(e["id"])
    if strict:
        ent = {e["id"]: e for e in intro if e["id"] in strict}
        key_of = {(e["registered"] or e.get("homonym_of_registered") or
                   (("hom:" + e["fold"]) if e["homonym_of"] else e["id"])): e for e in ent.values()}
        fresh = {i: {r: {"others": 0, "self": 0, "undetermined": 0} for r in ("evaluates_on", "trains_on")}
                 for i in strict}
        for f in sorted(OUT_DIR.glob("mentions_*.jsonl")):
            for r in read_jsonl(f):
                p = idx.get(r["arxiv_base"], {})
                for k, m in r["mentions"].items():
                    e = key_of.get(k)
                    if not e or not m.get("roles") or r["arxiv_base"] in {c["arxiv_base"] for c in e["claims"]}:
                        continue
                    if not any(_cites_paper(m.get("role_cites", []), c["title"]) for c in e["claims"]):
                        continue
                    devs = {_person(a) for a in e["developers"]}
                    users = {_person(a) for a in p.get("authors", [])}
                    who = "undetermined" if not devs or not users else ("self" if devs & users else "others")
                    for role in m["roles"]:
                        fresh[e["id"]][role][who] += 1
        for r in rows:
            if r["id"] in strict:
                r.update(fresh[r["id"]])
                r["counted_by"] = "citation of the introducing paper (the name was in use before its claim)"
        rows.sort(key=lambda r: (-r["evaluates_on"]["others"], r["id"]))
    out = OUT_DIR / "introduced_adoption.json"
    out.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    adopted = sum(1 for r in rows if r["evaluates_on"]["others"])
    print(f"adoption: {len(rows)} confirmed benchmarks ({len(strict)} counted by citation only); "
          f"{adopted} evaluated on by other authors -> {out.name}")
    return out




def summary() -> None:
    """Per edition: the denominator funnel, then how many papers state an
    evaluation benchmark, a training dataset, or a benchmark of their own."""
    from .corpus import EDITIONS, Corpus
    intro = load_json(INTRODUCED)["benchmarks"] if INTRODUCED.exists() else []
    new_by_ed = collections.Counter(e["first_claim"] for e in intro if not e.get("registered"))
    print(f"{'edition':13s} {'papers':>6s} {'arXiv':>6s} {'parsed':>6s} {'eval':>6s} {'train':>6s} "
          f"{'intro':>6s} {'new':>5s}")
    for disp, year in EDITIONS:
        c = Corpus(disp, year)
        res = ROOT / "data" / "raw" / "arxiv" / ("resolved.jsonl" if c.is_focus else f"resolved_{c.key}.jsonl")
        papers = sum(1 for _ in read_jsonl(c.papers))
        matched = len({r.get("arxiv_base") for r in read_jsonl(res)} - {None})
        ms = list(read_jsonl(OUT_DIR / f"mentions_{c.key}.jsonl"))
        has = lambda role: sum(1 for r in ms if any(role in m.get("roles", {}) for m in r["mentions"].values()))
        intro_papers = sum(1 for r in ms if r.get("introduces"))
        print(f"{disp + ' ' + str(year):13s} {papers:6d} {matched:6d} {len(ms):6d} {has('evaluates_on'):6d} "
              f"{has('trains_on'):6d} {intro_papers:6d} {new_by_ed[c.key]:5d}")
    print("parsed = the denominator (every parsed paper went through the rules); intro = papers claiming a "
          "benchmark or dataset of their own (any claim, reviewed or not); new = confirmed new benchmarks and "
          "datasets whose first claim in this corpus is here")


