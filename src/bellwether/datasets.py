"""Two CSVs a reader opens in a spreadsheet: which public benchmarks each
paper evaluated on, and which papers released a dataset or benchmark of
their own. Read-only over the processed corpus, and nothing here ranks —
rows come out in corpus order (guardrail 1).

    bellwether datasets [--out DIR]          # writes both files, prints coverage
    GET /datasets/benchmarks_used.csv         # the same two, served
    GET /datasets/datasets_released.csv

Two different facts from two different sources, and the boundary matters:

* benchmarks_used — names the ABSTRACT states (card_terms `d`) joined to OUR
  registry (config/benchmarks.json). Only a name with an API-checked home on
  the Hub or GitHub becomes a row; a name without one is counted in the
  summary, never listed as if it had an address.
* datasets_released — the paper's OWN links on GitHub or the Hub. Third-party
  repositories never reach resources.json's link lists (they are `mentions`),
  so every candidate is already the paper's; a row needs one of two further
  signals, and says which fired: the link IS a dataset (a Hub dataset, or a
  GitHub URL whose sentence speaks only of data), or the sentence — with the
  URL blanked, so a repo named "…-benchmark" cannot vouch for itself — says
  the paper introduces or releases a dataset/benchmark/corpus. The sentence
  is in the row; the reader judges.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from .mcp import Store, dataset_fold

USED_COLS = ["gid", "venue", "year", "title", "area", "benchmark", "as_written",
             "registry_kind", "host", "where", "hf", "gh", "paper_code"]
RELEASED_COLS = ["gid", "venue", "year", "title", "area", "url", "host", "repo",
                 "link_kind", "signal", "section", "evidence", "later", "stars",
                 "downloads", "likes", "license", "pushed", "modified", "checked",
                 "proposes"]

_HOST = {"gh": "github", "hfd": "huggingface"}       # the two the export admits
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
                                       "pushed", "modified")},
            "checked": m.get("at")}


def build(S: Store | None = None) -> tuple[list[list], list[list], dict]:
    """-> (benchmarks_used rows, datasets_released rows, summary)."""
    S = S or Store()
    papers = S.resources.get("papers") or {}
    n = len(S.union["keys"])
    used: list[list] = []
    released: list[list] = []
    sm = {"papers": n, "used_papers_naming": 0, "used_rows": 0, "used_unmapped": 0,
          "used_web_only": 0, "rel_papers": 0, "rel_rows": 0,
          "rel_by_signal": {"data-link": 0, "sentence": 0, "both": 0},
          "rel_skipped_host": 0, "link_index": bool(papers)}
    for gid in range(n):
        b = S.brief(gid)
        res = papers.get(str(gid)) or {}
        code = _code_url(res)
        # ---- 1. existing benchmarks the abstract says it evaluated on
        bl = S.benchmarks_of(gid)
        if bl:
            sm["used_papers_naming"] += 1
        for e in bl:
            if e.get("host") in ("huggingface", "github"):
                used.append([gid, b["venue"], b["year"], b.get("title", ""), b.get("area") or "",
                             e["name"], e["as_written"], e.get("kind") or "", e["host"],
                             e["where"], e.get("hf") or "", e.get("gh") or "", code])
                sm["used_rows"] += 1
            elif e.get("host") == "web":
                sm["used_web_only"] += 1
            else:
                sm["used_unmapped"] += 1
        # ---- 2. a dataset / benchmark of its own, released
        proposes = "; ".join(S.terms(*S.where(gid)).get("p") or [])
        had = False
        for kind in ("data", "code", "page", "model"):
            for it in res.get(kind) or []:
                if it.get("h") not in _HOST:
                    if kind == "data":
                        sm["rel_skipped_host"] += 1     # zenodo / osf: real, but not asked for
                    continue
                is_data = kind == "data"
                said = bool(_OWN.search(_URL.sub(" ", it.get("s") or "")))
                if not (is_data or said):
                    continue
                sig = "both" if (is_data and said) else ("data-link" if is_data else "sentence")
                m = _meta(it)
                released.append([gid, b["venue"], b["year"], b.get("title", ""), b.get("area") or "",
                                 it["u"], _HOST[it["h"]], it["r"], kind, sig, it.get("b") or "",
                                 it.get("s") or "", "yes" if "l" in it.get("w", "") else "",
                                 m.get("stars"), m.get("downloads"), m.get("likes"),
                                 m.get("license"), m.get("pushed"), m.get("modified"),
                                 m.get("checked"), proposes])
                sm["rel_rows"] += 1
                sm["rel_by_signal"][sig] += 1
                had = True
        if had:
            sm["rel_papers"] += 1
    return used, released, sm


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
        f"benchmark has an API-checked home on the Hub or GitHub.\n"
        f"  {sm['used_papers_naming']} of {sm['papers']} papers name a benchmark in the abstract; "
        f"{sm['used_unmapped']} mentions are names with no confirmed location and "
        f"{sm['used_web_only']} live only on a plain web page — neither is a row.\n"
        f"datasets_released.csv: {sm['rel_rows']} rows across {sm['rel_papers']} papers — the "
        f"paper's own GitHub/Hub link, kept because it is a dataset "
        f"({sm['rel_by_signal']['data-link']}), the sentence says the paper releases one "
        f"({sm['rel_by_signal']['sentence']}), or both ({sm['rel_by_signal']['both']}).\n"
        f"  Own links exist only where full text is on file; {sm['rel_skipped_host']} dataset "
        f"links on zenodo/osf were left out by the GitHub/Hub rule."
        + ("" if sm["link_index"] else
           "\n  NO LINK INDEX (resources.json missing): datasets_released is empty for that reason, "
           "not because nothing was released."))


def write(out_dir: Path, S: Store | None = None) -> dict:
    used, rel, sm = build(S)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "benchmarks_used.csv").write_text(to_csv(USED_COLS, used), encoding="utf-8")
    (out_dir / "datasets_released.csv").write_text(to_csv(RELEASED_COLS, rel), encoding="utf-8")
    return sm
