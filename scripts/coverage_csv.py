"""Write one installed revision's per-edition coverage as CSV (a data-release attachment).

    PYTHONPATH=src python3 scripts/coverage_csv.py --out FILE [--home DIR] [--revision ID]

Columns as in earlier releases: edition, listed_papers, arxiv_matched,
parsed_papers, field_labelled_papers. Values are paper-edition observations
as the revision's scope tool reports them; nothing is recomputed here.
"""
import argparse
import csv
import os
import sys

COLUMNS = ("edition", "listed_papers", "arxiv_matched", "parsed_papers", "field_labelled_papers")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--home")
    ap.add_argument("--revision")
    a = ap.parse_args()
    if a.home:
        os.environ["BENCH2AGENT_HOME"] = a.home
    from bench2agent.core.benchmarks import BenchmarkStore
    from bench2agent.core.paths import ROOT
    store = BenchmarkStore(ROOT, revision=a.revision)
    scope = store.scope({})
    if not scope.get("available"):
        print(scope.get("reason") or "revision not available", file=sys.stderr)
        return 1
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(COLUMNS)
        for ed in sorted(scope["editions"]):
            e = scope["editions"][ed]
            w.writerow([ed, e["total_papers"], e["arxiv_matched"], e["parsed_papers"], e["field_labelled_papers"]])
    print(f"{a.out}: {len(scope['editions'])} editions of revision {store.revision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
