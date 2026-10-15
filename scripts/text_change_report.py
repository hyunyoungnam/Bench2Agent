"""For papers whose HTML changed between two archived versions, say whether
the PARSED text changed too, with one parser for both versions, so a text
change is told apart from a parser change.

    PYTHONPATH=src <python with lxml> scripts/text_change_report.py [--home DIR]

Reads the working database for papers with a confirmed content hash, finds
the previous archived version (`<base>.<sha12>.html.gz` other than the
current), parses both with `icml.html_extract.parse_html` and compares the
section texts. Prints per edition: HTML hash changed / parsed text changed /
sections changed, and the introduction-claim sentences that differ.
"""
import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--home")
    a = ap.parse_args()
    root = Path(a.home) if a.home else Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from icml.html_extract import parse_html
    from icml.bench_mentions import introduced_names
    con = sqlite3.connect(root / "data/work/pipeline.sqlite")
    rows = con.execute("SELECT edition, arxiv_base, content_sha256 FROM paper_stages WHERE content_sha256 IS NOT NULL ORDER BY edition, arxiv_base").fetchall()
    archive = root / "data/raw/html"
    per = {}
    claim_diffs = []
    for edition, base, sha in rows:
        versions = sorted(p for p in archive.glob(f"{base}.*.html.gz") if p.name != f"{base}.html.gz")
        current = archive / f"{base}.{sha[:12]}.html.gz"
        previous = [p for p in versions if p != current]
        if not current.exists() or not previous:
            per.setdefault(edition, {"html_changed": 0, "no_previous_version": 0, "text_changed": 0, "claims_changed": 0})["no_previous_version"] += 1
            continue
        old_html = gzip.decompress(previous[-1].read_bytes()).decode("utf-8", "replace")
        new_html = gzip.decompress(current.read_bytes()).decode("utf-8", "replace")
        stat = per.setdefault(edition, {"html_changed": 0, "no_previous_version": 0, "text_changed": 0, "claims_changed": 0})
        stat["html_changed"] += 1
        old_p, new_p = parse_html(old_html), parse_html(new_html)
        old_text = "\n".join(s["text"] for s in old_p["sections"]); new_text = "\n".join(s["text"] for s in new_p["sections"])
        if old_text != new_text:
            stat["text_changed"] += 1
        old_claims = claim_sentences(old_p, introduced_names)
        new_claims = claim_sentences(new_p, introduced_names)
        gone, new = sorted(set(old_claims) - set(new_claims)), sorted(set(new_claims) - set(old_claims))
        reworded = sorted(n for n in set(old_claims) & set(new_claims) if old_claims[n] != new_claims[n])
        if gone or new or reworded:
            stat["claims_changed"] += 1
            claim_diffs.append((edition, base, gone, new, reworded))
    print(f"{'edition':13}{'html changed':>13}{'text changed':>13}{'claims changed':>15}{'no prev.':>10}")
    tot = {"html_changed": 0, "text_changed": 0, "claims_changed": 0, "no_previous_version": 0}
    for ed, st in sorted(per.items()):
        print(f"{ed:13}{st['html_changed']:>13}{st['text_changed']:>13}{st['claims_changed']:>15}{st['no_previous_version']:>10}")
        for k in tot:
            tot[k] += st[k]
    print("TOTAL", tot)
    for ed, base, gone, new, reworded in claim_diffs:
        print(f"  {ed} {base}: claim names gone {gone} new {new}; sentence changed {reworded}")
    return 0


def claim_sentences(parsed, fn) -> dict:
    """name fold -> the first sentence that claims it, as the mention rule reads the text."""
    from icml.bench_mentions import dataset_key
    out = {}
    for s in parsed["sections"]:
        for name, _noun, sent in introduced_names_in(s["text"], fn):
            out.setdefault(dataset_key(name), sent)
    return out


def introduced_names_in(text, fn):
    import re
    out = []
    for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", text):
        try:
            for name, noun in fn(sent):
                out.append((name, noun, sent))
        except Exception:  # noqa: BLE001 — a sentence the rule cannot read
            pass
    return out


if __name__ == "__main__":
    sys.exit(main())
