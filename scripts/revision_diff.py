"""Stepwise diff of two installed revisions: per edition papers (from the
pipeline files when present), arXiv matches, parsed observations, field
labels, then usage and adoption on a few representative calls.

    PYTHONPATH=src python3 scripts/revision_diff.py OLD_ID NEW_ID [--home DIR] [--old-home DIR]

A difference between two revisions for one edition is a data or rule
change (identity, parsing, labels, review), never a usage trend: trends
compare editions inside one revision.
"""
import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path


def ed_of(name: str) -> str:
    if name == "papers.jsonl":
        return "icml-2026"
    m = re.match(r"papers_(\d{4})\.jsonl", name)
    if m:
        return f"icml-{m.group(1)}"
    m = re.match(r"papers_([a-z]+)_(\d{4})\.jsonl", name)
    return f"{m.group(1)}-{m.group(2)}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--home", help="home holding the new revision (default: this checkout)")
    ap.add_argument("--old-home", help="home holding the old revision (default: --home)")
    a = ap.parse_args()
    from bench2agent.core.benchmarks import BenchmarkStore
    from bench2agent.core.paths import ROOT
    new_home = Path(a.home) if a.home else ROOT
    old_home = Path(a.old_home) if a.old_home else new_home
    A, N = BenchmarkStore(old_home, revision=a.old), BenchmarkStore(new_home, revision=a.new)
    ea, en = A.data()["editions"], N.data()["editions"]
    print(f"old {a.old}  new {a.new}")
    print(f"{'edition':13}{'papers':>14}{'matched':>14}{'parsed':>14}{'unident.':>9}{'field-lab.':>13}  notes")
    tot = {"parsed_old": 0, "parsed_new": 0, "labelled_old": 0, "labelled_new": 0}
    for ed in sorted(set(ea) | set(en)):
        pa, pn = ea.get(ed, {}), en.get(ed, {})
        notes = []
        papers = ROOT / "data/processed" / ("papers.jsonl" if ed == "icml-2026" else
                                              f"papers_{ed.split('-')[1]}.jsonl" if ed.startswith("icml-") else f"papers_{ed.replace('-', '_')}.jsonl")
        if papers.exists():
            rows = [json.loads(l) for l in open(papers, encoding="utf-8")]
            cand = sum(1 for r in rows if r.get("identity_candidates")); conf = sum(1 for r in rows if r.get("identity_conflict"))
            multi = sum(1 for r in rows if len(r.get("titles", [])) > 1)
            notes += [f"{cand} title-candidates"] * bool(cand) + [f"{conf} id-conflicts"] * bool(conf) + [f"{multi} retitled merges"] * bool(multi)
        for k, src in (("parsed_old", pa), ("parsed_new", pn)):
            tot[k] += src.get("parsed_papers") or 0
        tot["labelled_old"] += pa.get("field_labelled_papers") or 0
        tot["labelled_new"] += pn.get("field_labelled_papers") or 0
        changed = any(pa.get(k) != pn.get(k) for k in ("total_papers", "arxiv_matched", "parsed_papers", "field_labelled_papers"))
        if changed or notes:
            print(f"{ed:13}{str(pa.get('total_papers'))+'->'+str(pn.get('total_papers')):>14}"
                  f"{str(pa.get('arxiv_matched'))+'->'+str(pn.get('arxiv_matched')):>14}"
                  f"{str(pa.get('parsed_papers'))+'->'+str(pn.get('parsed_papers')):>14}{str(pn.get('parsed_unidentified') or 0):>9}"
                  f"{str(pa.get('field_labelled_papers'))+'->'+str(pn.get('field_labelled_papers')):>13}  {', '.join(notes)}")
    print("TOTAL", tot)
    for args in ({}, {"topic": "robotics"}, {"topic": "computer vision"}, {"latest": False}):
        ua, un = A.usage(args), N.usage(args)
        print("usage", args, "parsed", ua.get("coverage", {}).get("parsed_papers"), "->", un.get("coverage", {}).get("parsed_papers"),
              "| top3", [(r["name"], r["papers"]) for r in ua.get("results", [])[:3]], "->", [(r["name"], r["papers"]) for r in un.get("results", [])[:3]])
    ta, tb = A.trend({"benchmark": "cifar-10", "venue": "iclr"}), N.trend({"benchmark": "cifar-10", "venue": "iclr"})
    print("ICLR CIFAR-10:", [(r["edition"], r["papers"], r["parsed_papers"]) for r in tb["results"]],
          "(unchanged)" if ta["results"] == tb["results"] else "(CHANGED from " + str([(r["edition"], r["papers"], r["parsed_papers"]) for r in ta["results"]]) + ")")
    for bid in ("libero", "mt-bench", "relbench", "relbench-2"):
        out = []
        for S in (A, N):
            try:
                ad = S.adoption({"benchmark": bid})
                out.append((ad.get("evaluates_on"), ad.get("unattributed_using_papers"), ad.get("error")))
            except ValueError as exc:
                out.append(str(exc)[:50])
        print("adoption", bid, out[0], "->", out[1])
    cat_a = {e["id"] for e in A.data()["benchmarks"]}; cat_n = {e["id"] for e in N.data()["benchmarks"]}
    print("catalogue:", len(cat_a), "->", len(cat_n), "| added:", sorted(cat_n - cat_a)[:20], "| removed:", sorted(cat_a - cat_n)[:20])
    na, nn = A.new({"limit": 1}), N.new({"limit": 1})
    print("introduced benchmarks total:", na["total"], "->", nn["total"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
