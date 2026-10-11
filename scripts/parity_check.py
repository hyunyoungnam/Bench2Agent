"""Compare the six tools on JSON and SQLite over the installed snapshot.

    PYTHONPATH=src python3 scripts/parity_check.py [--home DIR]

Prints, per call, whether both backends returned the same JSON and how long
each took; then the revision-specific regression figures for snapshot
0dd4b9bbee4b54d2 when that is the installed one. Exit status 1 on any
mismatch.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

CALLS = [
    ("scope", {}), ("scope", {"query": "robot"}),
    ("usage", {}), ("usage", {"latest": False}), ("usage", {"topic": "robotics"}),
    ("usage", {"topic": "robotics", "role": "trains_on"}), ("usage", {"venue": "iclr", "latest": False, "limit": 50}),
    ("usage", {"edition": "corl-2024"}), ("usage", {"years": [2023], "limit": 25}),
    ("trend", {"benchmark": "cifar-10", "venue": "iclr"}), ("trend", {"benchmark": "libero"}),
    ("trend", {"benchmark": "imagenet-1k", "role": "trains_on"}), ("trend", {"benchmark": "mmlu", "topic": "robotics"}),
    ("evidence", {"benchmark": "libero", "topic": "robotics", "limit": 5}), ("evidence", {"benchmark": "gsm8k", "offset": 40, "limit": 20}),
    ("evidence", {"benchmark": "air-bench-2"}),
    ("adoption", {"benchmark": "libero"}), ("adoption", {"benchmark": "mt-bench"}), ("adoption", {"benchmark": "air-bench"}),
    ("adoption", {"benchmark": "gaia"}), ("adoption", {"benchmark": "mmmu", "venue": "cvpr"}),
    ("new", {}), ("new", {"sort": "adoption", "limit": 20}), ("new", {"kind": "dataset", "include_adoption": True, "limit": 20}),
    ("new", {"topic": "robotics", "include_adoption": True}), ("new", {"venue": "corl", "years": [2024]}),
]
ERRORS = [("trend", {"benchmark": "Mirage"}), ("usage", {"topic": "nowhere"}), ("adoption", {"benchmark": "meta-world"})]


def call(store, name, args):
    t = time.perf_counter()
    try:
        out = getattr(store, name)(dict(args))
    except ValueError as exc:
        out = {"ValueError": str(exc)}
    return out, time.perf_counter() - t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--home")
    a = ap.parse_args()
    if a.home:
        os.environ["BENCH2AGENT_HOME"] = a.home
    from bench2agent.core.benchmarks import BenchmarkStore
    from bench2agent.core.paths import ROOT
    js, db = BenchmarkStore(ROOT, backend="json"), BenchmarkStore(ROOT, backend="sqlite")
    t = time.perf_counter(); js.data(); load_json = time.perf_counter() - t
    t = time.perf_counter(); db.data(); load_db = time.perf_counter() - t
    print(f"snapshot {js.data()['snapshot_id']}  json load {load_json:.2f}s  sqlite open {load_db:.2f}s")
    bad = 0
    print(f"{'tool':9} {'args':62} {'same':5} {'json':>7} {'sqlite':>7}")
    for name, args in CALLS + ERRORS:
        x, tx = call(js, name, args)
        y, ty = call(db, name, args)
        same = json.dumps(x, sort_keys=True) == json.dumps(y, sort_keys=True)
        bad += not same
        print(f"{name:9} {json.dumps(args)[:62]:62} {'yes' if same else 'NO':5} {tx:6.2f}s {ty:6.2f}s")
    if js.data()["snapshot_id"] == "0dd4b9bbee4b54d2":
        print("regression figures on 0dd4b9bbee4b54d2 (sqlite):")
        u = db.usage({"topic": "robotics"})
        top = {r["name"]: r["papers"] for r in u["results"][:3]}
        print(f"  robotics/latest parsed {u['coverage']['parsed_papers']} (expect 509); top {top} (expect LIBERO 80, SIMPLER 41, Meta-World 29)")
        tr = db.trend({"benchmark": "cifar-10", "venue": "iclr"})
        print("  ICLR CIFAR-10:", ", ".join(f"{r['edition']} {r['papers']}/{r['parsed_papers']:,}" for r in tr["results"]),
              "(expect 178/1,888, 197/3,038, 165/4,237)")
    print("mismatches:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
