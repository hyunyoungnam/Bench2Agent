"""Three CSVs a reader opens in a spreadsheet: which public benchmarks each
paper's abstract names, and which papers released a dataset or benchmark of
their own. Read-only over the processed corpus, and nothing here ranks —
rows come out in corpus order (guardrail 1).

    bellwether datasets [--out DIR] [--gid N] # writes files, prints coverage
    GET /datasets/benchmarks_used.csv         # the same files, served
    GET /datasets/datasets_released.csv
    GET /datasets/benchmarks_introduced.csv

Two different facts from two different sources, and the boundary matters:

* benchmarks_used — names the ABSTRACT states (card_terms `d`) joined to OUR
  registry (config/benchmarks.json). Only a name with a confirmed home
  becomes a row; a name without one is counted in the summary. A row says the
  abstract NAMES the benchmark — not that the paper evaluated on it, trained
  on it, or introduced it; the `relation` column carries exactly that, so the
  file still says what it means once it leaves the page. The dedicated
  full-text pass will add evaluates_on / trains_on / introduces.
* datasets_released — the paper's OWN links on supported hosts. Third-party
  repositories never reach resources.json's link lists (they are `mentions`),
  so every candidate is already the paper's; a row needs one of two further
  signals, and says which fired: the link IS a dataset (a Hub dataset, or a
  GitHub URL whose sentence speaks only of data), or the sentence — with the
  URL blanked, so a repo named "…-benchmark" cannot vouch for itself — says
  the paper introduces or releases a dataset/benchmark/corpus. The sentence
  is in the row; the reader judges.
* benchmarks_introduced — a strict subset of datasets_released where the
  paper claims a new benchmark/suite/testbed and the release slug occurs in
  that claim. It is not independent proof of chronological priority.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from .mcp import Store, dataset_fold

USED_COLS = ["gid", "venue", "year", "title", "area", "benchmark", "as_written",
             "registry_kind", "host", "where", "hf", "gh", "paper_code", "relation"]
RELEASED_COLS = ["gid", "venue", "year", "title", "area", "url", "host", "repo",
                 "link_kind", "signal", "section", "evidence", "later", "stars",
                 "downloads", "likes", "license", "pushed", "modified", "checked",
                 "proposes", "gated"]
INTRODUCED_COLS = RELEASED_COLS + ["claim_source", "claim_evidence"]

_HOST = {"gh": "github", "hfd": "huggingface", "gl": "gitlab",
         "zenodo": "zenodo", "osf": "osf", "page": "website", "hfs": "huggingface"}
_URL = re.compile(r"https?://\S+")
# The paper's own release of a dataset-like thing, in its own words. Three
# shapes: "we introduce/release … a benchmark", "our dataset / a new corpus",
# "the benchmark is publicly available". Verbs are stems so tense does not
# matter; the window is bounded so a later clause about someone else's data
# does not bleed in.
_OWN = re.compile(
    r"\b(we|our)\b[^.;]{0,80}?\b(introduc|propos|releas|construct|curat|collect|"
    r"build|built|present|creat|annotat|assembl|compil|contribut)\w*[^.;]{0,80}?"
    r"\b(dataset|datasets|benchmark|benchmarks|corpus|corpora|suite|testbed)\b"
    r"|\b(our|the proposed|a new|a novel|this new)\s+(\w+\s+){0,2}"
    r"(dataset|benchmark|corpus|suite|testbed)\b"
    r"|\b(dataset|benchmark|corpus|suite|testbed)s?\b[^.;]{0,50}?"
    r"\b(is|are|will be)\s+(publicly\s+|made\s+|now\s+)?(available|released|open-?sourced)",
    re.I)
_INTRO_BENCH = re.compile(
    r"\b(?:we|this paper)\b[^.;]{0,90}?\b(?:introduc|propos|creat|develop|"
    r"present|construct)\w*\b[^.;]{0,90}?\b(?:benchmark|suite|testbed)\b"
    r"|\b(?:our|a|the)\s+(?:new|novel)\s+(?:[\w-]+\s+){0,4}"
    r"(?:benchmark|suite|testbed)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_EVALUATE_ON = re.compile(r"\b(?:evaluat|test)\w*\b[^.;]{0,30}?\b(?:on|using|against)\b", re.I)
_GENERIC_SLUG = {"benchmark", "benchmarks", "dataset", "datasets", "data",
                 "code", "official", "project", "website", "paper"}


def _intro_claim(abstract: str, evidence: str, repo: str) -> tuple[str, str] | None:
    """Return a matching paper claim, not an assertion of first invention."""
    slug = re.sub(r"[^a-z0-9]", "", repo.rsplit("/", 1)[-1].split(".")[0].lower())
    if len(slug) < 4 or slug in _GENERIC_SLUG:
        return None
    for source, text in (("abstract", abstract), ("release sentence", evidence)):
        clean = _URL.sub(" ", text or "")
        for sentence in _SENTENCE.split(clean):
            for claim in _INTRO_BENCH.finditer(sentence):
                match_text = claim.group()
                if _EVALUATE_ON.search(match_text):
                    continue
                if slug in re.sub(r"[^a-z0-9]", "", match_text.lower()):
                    return source, sentence.strip()
    return None


def _code_url(res: dict) -> str:
    for it in res.get("code") or []:
        if it.get("h") == "gh":
            return it["u"]
    return ""


def _meta(it: dict) -> dict:
    m = it.get("m") or {}
    if m.get("gone"):
        return {"checked": m.get("at"), "license": "not found"}
    return {**{k: m.get(k) for k in ("stars", "downloads", "likes", "license",
                                       "pushed", "modified", "gated")},
            "checked": m.get("at")}


def build(S: Store | None = None, gid: int | None = None) -> tuple[list[list], list[list], list[list], dict]:
    """-> (benchmarks_used, datasets_released, benchmarks_introduced, summary)."""
    S = S or Store()
    papers = S.resources.get("papers") or {}
    n = len(S.union["keys"])
    fulltext = set(S.resources.get("fulltext_gids") or [])
    used: list[list] = []
    released: list[list] = []
    introduced: list[list] = []
    if gid is not None and not (0 <= gid < n):
        raise ValueError(f"gid must be in 0..{n - 1}")
    sm = {"papers": 1 if gid is not None else n,
          "fulltext_papers": len(fulltext) if gid is None else int(gid in fulltext),
          "used_papers_naming": 0, "used_rows": 0, "used_unmapped": 0,
          "used_web_only": 0, "rel_papers": 0, "rel_rows": 0,
          "rel_by_signal": {"data-link": 0, "sentence": 0, "both": 0},
          "introduced_rows": 0, "rel_skipped_host": 0, "link_index": bool(papers)}
    for gid in (range(n) if gid is None else (gid,)):
        b = S.brief(gid)
        res = papers.get(str(gid)) or {}
        code = _code_url(res)
        # ---- 1. existing benchmarks the abstract names
        bl = S.benchmarks_of(gid)
        if bl:
            sm["used_papers_naming"] += 1
        for e in bl:
            if e.get("where") and e.get("host"):
                used.append([gid, b["venue"], b["year"], b.get("title", ""), b.get("area") or "",
                             e["name"], e["as_written"], e.get("kind") or "", e["host"],
                             e["where"], e.get("hf") or "", e.get("gh") or "", code,
                             "named_in_abstract"])
                sm["used_rows"] += 1
                if e["host"] == "web":
                    sm["used_web_only"] += 1
            else:
                sm["used_unmapped"] += 1
        # ---- 2. a dataset / benchmark of its own, released
        proposes = "; ".join(S.terms(*S.where(gid)).get("p") or [])
        had = False
        for kind in ("data", "code", "page"):
            for it in res.get(kind) or []:
                if it.get("h") not in _HOST:
                    sm["rel_skipped_host"] += 1
                    continue
                is_data = kind == "data"
                sentence = _URL.sub(" ", it.get("s") or "")
                said = bool(_OWN.search(sentence))
                if not (is_data or said):
                    continue
                sig = "both" if (is_data and said) else ("data-link" if is_data else "sentence")
                m = _meta(it)
                row = [gid, b["venue"], b["year"], b.get("title", ""), b.get("area") or "",
                       it["u"], _HOST[it["h"]], it["r"], kind, sig, it.get("b") or "",
                       it.get("s") or "", "yes" if "l" in it.get("w", "") else "",
                       m.get("stars"), m.get("downloads"), m.get("likes"),
                       m.get("license"), m.get("pushed"), m.get("modified"),
                       m.get("checked"), proposes, m.get("gated")]
                released.append(row)
                sm["rel_rows"] += 1
                sm["rel_by_signal"][sig] += 1
                claim = _intro_claim((S.rec(gid) or {}).get("abstract") or "",
                                     it.get("s") or "", it["r"])
                if claim:
                    introduced.append(row + list(claim))
                    sm["introduced_rows"] += 1
                had = True
        if had:
            sm["rel_papers"] += 1
    return used, released, introduced, sm


def to_csv(cols: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue()


def summary_text(sm: dict) -> str:
    """Coverage, stated — the CSVs list what has an address, this says what does not."""
    return (
        f"benchmarks_used.csv: {sm['used_rows']} rows — a (paper, benchmark) pair where the "
        f"abstract names the benchmark and it has a registry home on the Hub, GitHub, or a "
        f"website. Named, not verified as evaluated on.\n"
        f"  {sm['used_papers_naming']} of {sm['papers']} papers name a benchmark in the abstract; "
        f"{sm['used_unmapped']} mentions are names with no confirmed location and "
        f"{sm['used_web_only']} rows live on a plain web page.\n"
        f"datasets_released.csv: {sm['rel_rows']} rows across {sm['rel_papers']} papers — the "
        f"paper's own supported-host link, kept because it is a data link "
        f"({sm['rel_by_signal']['data-link']}), the sentence says the paper releases one "
        f"({sm['rel_by_signal']['sentence']}), or both ({sm['rel_by_signal']['both']}).\n"
        f"  Full text is on file for {sm['fulltext_papers']} of {sm['papers']} papers; "
        f"{sm['rel_skipped_host']} "
        f"links have an unsupported host.\n"
        f"benchmarks_introduced.csv: {sm['introduced_rows']} candidate rows where the "
        f"paper's own sentence claims it introduced a benchmark, suite, or testbed."
        + ("" if sm["link_index"] else
           "\n  NO LINK INDEX (resources.json missing): datasets_released is empty for that reason, "
           "not because nothing was released."))


def write(out_dir: Path, S: Store | None = None, gid: int | None = None) -> dict:
    used, rel, intro, sm = build(S, gid)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "benchmarks_used.csv").write_text(to_csv(USED_COLS, used), encoding="utf-8")
    (out_dir / "datasets_released.csv").write_text(to_csv(RELEASED_COLS, rel), encoding="utf-8")
    (out_dir / "benchmarks_introduced.csv").write_text(
        to_csv(INTRODUCED_COLS, intro), encoding="utf-8")
    return sm
