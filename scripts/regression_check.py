"""Run the six tools on the installed revision and check pinned figures.

    PYTHONPATH=src python3 scripts/regression_check.py [--home DIR] [--revision ID]

Prints, per call, the time taken and whether the call returned a result (or
the expected error); then the revision-specific regression figures for the
installed revision when they are known. A wrong figure fails the run even
though every call succeeded, so a counting bug cannot pass as "it runs".
Exit status 1 on any failure.
"""
import argparse
import json
import os
import sys
import time

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

# Pinned figures per revision: measured once on that revision, never a
# prediction for the next one. Add a block for every published revision.
FIGURES = {
    "0dd4b9bbee4b54d2": {
        "robotics_latest": {"parsed": 509, "LIBERO": 80, "SIMPLER": 41, "Meta-World": 29},
        "iclr_cifar10": [("iclr-2024", 178, 1888), ("iclr-2025", 197, 3038), ("iclr-2026", 165, 4237)],
    },
    "e41da62f2fb3616f": {
        "robotics_latest": {"parsed": 509, "LIBERO": 80, "SIMPLER": 41, "Meta-World": 29},
        "iclr_cifar10": [("iclr-2020", 103, 547), ("iclr-2021", 135, 717), ("iclr-2022", 141, 838), ("iclr-2023", 166, 1241),
                         ("iclr-2024", 178, 1888), ("iclr-2025", 197, 3038), ("iclr-2026", 165, 4237)],
        "latest_all": {"parsed": 27316, "GSM8K": 1111, "CIFAR-10": 840, "MMLU": 797},
        "libero_adoption": {"others": 193, "self": 3, "undetermined": 0, "before_claim": 0},
    },
}
# The same extraction built directly as a database (2026-10-13) hashes to this
# content id; its figures are, by construction, those of e41da62f2fb3616f.
FIGURES["79b1b3c4d4de9d9c"] = FIGURES["e41da62f2fb3616f"]
# 2026-10-13, identity rules and label provenance (schema 2): the headline
# figures did not move (robotics has no venue-track or volume labels among
# the latest editions); computer vision/latest grew with the AAAI tracks.
FIGURES["16b704077d97298a"] = {**FIGURES["e41da62f2fb3616f"],
                               "computer_vision_latest": {"parsed": 2816, "COCO": 163, "POPE": 75, "Video-MME": 69}}
# 2026-10-13, id-first paper identity and candidate-only benchmark identity:
# 87,043 observations; the headline figures hold; RelBench's uses split into
# citation-attributed (relbench-2) and unattributed ones.
# 2026-10-14, first incremental run: 700 newly parsed papers (matched after the
# earlier parses, or matched by the backfilled index) joined the observations.
FIGURES["a4a18c3dbaf3ca0b"] = {
    "robotics_latest": {"parsed": 512, "LIBERO": 81, "SIMPLER": 41, "Meta-World": 29},
    "iclr_cifar10": [("iclr-2020", 103, 547), ("iclr-2021", 135, 717), ("iclr-2022", 141, 838), ("iclr-2023", 166, 1241),
                     ("iclr-2024", 179, 1894), ("iclr-2025", 196, 3057), ("iclr-2026", 167, 4248)],
    "latest_all": {"parsed": 27443, "GSM8K": 1120, "CIFAR-10": 843, "MMLU": 800},
    "computer_vision_latest": {"parsed": 2826, "COCO": 163, "POPE": 75, "Video-MME": 69},
    "libero_adoption": {"others": 194, "self": 3, "undetermined": 0, "before_claim": 0},
    "relbench_2_adoption": {"others": 3, "self": 1, "undetermined": 0, "unattributed": 8},
}
# 2026-10-14, second incremental run: the 375 AAAI 2020–23 and CoRL 2020–22
# papers arXiv had rate-limited; no latest edition changed.
FIGURES["e203ef9d830c7254"] = dict(FIGURES["a4a18c3dbaf3ca0b"])
FIGURES["7607ce005c52fba5"] = {**FIGURES["16b704077d97298a"],
                               # ICLR 2025: two retitled Oral/Poster pairs were two observations each; one used CIFAR-10
                               "iclr_cifar10": [("iclr-2020", 103, 547), ("iclr-2021", 135, 717), ("iclr-2022", 141, 838), ("iclr-2023", 166, 1241),
                                                ("iclr-2024", 178, 1888), ("iclr-2025", 196, 3036), ("iclr-2026", 165, 4237)],
                               "relbench_2_adoption": {"others": 3, "self": 1, "undetermined": 0, "unattributed": 8}}


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
    ap.add_argument("--revision")
    a = ap.parse_args()
    if a.home:
        os.environ["BENCH2AGENT_HOME"] = a.home
    from bench2agent.core.benchmarks import BenchmarkStore
    from bench2agent.core.paths import ROOT
    store = BenchmarkStore(ROOT, revision=a.revision)
    t = time.perf_counter()
    data = store.data()
    print(f"revision {store.revision}  snapshot {data.get('snapshot_id')}  open {time.perf_counter() - t:.2f}s  available {data['available']}")
    if not data["available"]:
        print(data.get("reason"))
        return 1
    bad = 0
    print(f"{'tool':9} {'args':62} {'ok':4} {'time':>7}")
    for name, args in CALLS:
        out, dt = call(store, name, args)
        ok = "ValueError" not in out and "error" not in out
        bad += not ok
        print(f"{name:9} {json.dumps(args)[:62]:62} {'yes' if ok else 'NO':4} {dt:6.2f}s")
    for name, args in ERRORS:
        out, dt = call(store, name, args)
        ok = "ValueError" in out or "error" in out
        bad += not ok
        print(f"{name:9} {json.dumps(args)[:62]:62} {'err' if ok else 'NO':4} {dt:6.2f}s")
    want = FIGURES.get(data.get("snapshot_id"))
    if want:
        print(f"regression figures on {data['snapshot_id']}:")
        u = store.usage({"topic": "robotics"})
        got = {"parsed": u["coverage"]["parsed_papers"], **{r["name"]: r["papers"] for r in u["results"][:3]}}
        ok = got == want["robotics_latest"]
        bad += not ok
        print(f"  robotics/latest {got} {'ok' if ok else 'EXPECTED ' + str(want['robotics_latest'])}")
        tr = store.trend({"benchmark": "cifar-10", "venue": "iclr"})
        got_tr = [(r["edition"], r["papers"], r["parsed_papers"]) for r in tr["results"]]
        ok = got_tr == want["iclr_cifar10"]
        bad += not ok
        print(f"  ICLR CIFAR-10 {got_tr} {'ok' if ok else 'EXPECTED ' + str(want['iclr_cifar10'])}")
        if "latest_all" in want:
            u = store.usage({})
            got = {"parsed": u["coverage"]["parsed_papers"], **{r["name"]: r["papers"] for r in u["results"][:3]}}
            ok = got == want["latest_all"]
            bad += not ok
            print(f"  all venues/latest {got} {'ok' if ok else 'EXPECTED ' + str(want['latest_all'])}")
        if "computer_vision_latest" in want:
            u = store.usage({"topic": "computer vision"})
            got = {"parsed": u["coverage"]["parsed_papers"], **{r["name"]: r["papers"] for r in u["results"][:3]}}
            ok = got == want["computer_vision_latest"]
            bad += not ok
            print(f"  computer vision/latest {got} {'ok' if ok else 'EXPECTED ' + str(want['computer_vision_latest'])}")
        if "relbench_2_adoption" in want:
            ad = store.adoption({"benchmark": "relbench-2"})
            got = {**ad["evaluates_on"], "unattributed": ad["unattributed_using_papers"]}
            ok = got == want["relbench_2_adoption"]
            bad += not ok
            print(f"  RelBench (benchmark paper) adoption {got} {'ok' if ok else 'EXPECTED ' + str(want['relbench_2_adoption'])}")
        if "libero_adoption" in want:
            ad = store.adoption({"benchmark": "libero"})
            got = {**ad["evaluates_on"], "before_claim": ad["before_claim"]}
            ok = got == want["libero_adoption"]
            bad += not ok
            print(f"  LIBERO adoption {got} {'ok' if ok else 'EXPECTED ' + str(want['libero_adoption'])}")
    else:
        print(f"no pinned figures for {data.get('snapshot_id')}; add them to FIGURES after measuring once")
    print("failures:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
