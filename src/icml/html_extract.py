"""Full text from arXiv's native HTML — the primary route (decided 2026-08-25).

Measured against the PDF route on 50 papers: HTML available 98%, line-break
hyphen artifacts 264 -> 3 per 100k chars (the noise class that faked the 40.7%
"hallucination rate"), and ~2.4x the retained text. Two gates before this
becomes primary (CLAUDE.md > Full text routes):

  1. refind >= 95% of PDF-verified spans on a sample;
  2. PRESERVE the references section, one entry per cited work — the PDF
     pipeline's 60% filter dropped references entirely, which is why no
     citation data exists.

Output is the sectioned JSONL (same schema as icml.pdf_extract plus
`references`, an `appendix` bucket where NeurIPS hides its limitations
sections, and `floats`: every captioned table and figure with its caption and,
for tables, the cell text). With --archive the raw page is also kept gzipped
(decided 2026-08-28), and --reparse rebuilds the JSONL from that archive
without touching arXiv — how a parser change reaches every corpus.

    .venv/bin/python -m icml.html_extract --resolved data/raw/arxiv/resolved_neurips-2025.jsonl \\
        --out data/interim/fulltext_html_neurips_2025.jsonl --workers 4

    .venv/bin/python -m icml.html_extract --resolved data/raw/arxiv/resolved_iclr-2025.jsonl \\
        --out data/interim/fulltext_html_iclr_2025.jsonl --archive data/raw/html --reparse

Resumable: rows already in --out are skipped.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from lxml import html as lhtml

from .common import RAW, SSL_CTX, read_jsonl
from .pdf_extract import bucket_for

RESOLVED = RAW / "arxiv" / "resolved.jsonl"
UA = {"User-Agent": "whatsnewai/1.0 (research tool; contact via arXiv account)"}

# Buckets the PDF route keeps, plus appendix: LaTeXML marks appendices
# explicitly, and NeurIPS puts its limitations there.
KEEP = {"abstract", "intro", "background", "method", "experiments", "conclusion",
        "appendix"}

_NUM = re.compile(r"^\s*(?:[A-Z]|\d+)(?:\.\d+)*[\.\)]?\s+")
_WS = re.compile(r"\s+")


def _clean(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


def fetch_html(base: str, timeout: int = 30) -> str | None:
    """The latest HTML rendering, or None when arXiv has none (~2%)."""
    req = urllib.request.Request(f"https://arxiv.org/html/{base}", headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as fh:
            return fh.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def parse_html(text: str) -> dict:
    """LaTeXML page -> {sections, references, stats}. Never archives the HTML."""
    doc = lhtml.fromstring(text)
    art = doc.xpath("//article") or [doc]
    root = art[0]

    # MathML text ("O(1)") matches PDF-rendered math far better than the LaTeX
    # source ("\\mathcal{O}(1)") — drop the annotation carrying the source.
    for ann in root.xpath(".//*[local-name()='annotation' or local-name()='annotation-xml']"):
        ann.getparent().remove(ann)
    # Floats are kept OUT of the prose but not discarded: a benchmark is often
    # named only in a table header or a caption ("Table 3: Results on GSM8K
    # and MATH-500"), so they go to their own list before removal.
    floats = _floats(root)
    # Floats and footnotes are layout, not prose.
    for el in root.xpath(".//figure | .//*[contains(@class,'ltx_table')]"
                         " | .//*[contains(@class,'ltx_note')]"):
        if el.getparent() is not None:
            el.getparent().remove(el)

    sections: list[dict] = []
    for ab in root.xpath(".//div[contains(@class,'ltx_abstract')]"):
        t = _clean(ab.text_content())
        t = re.sub(r"^\s*Abstract[\.:]?\s*", "", t, flags=re.I)
        if t:
            sections.append({"bucket": "abstract", "title": "Abstract", "text": t})

    for sec in root.xpath(".//section[contains(@class,'ltx_section')"
                          " or contains(@class,'ltx_appendix')]"):
        cls = sec.get("class") or ""
        # top-level only: subsections ride inside their parent's text_content
        parent = sec.getparent()
        if parent is not None and parent.tag == "section":
            continue
        titles = sec.xpath(".//*[contains(@class,'ltx_title')]")
        name = _NUM.sub("", _clean(titles[0].text_content())) if titles else ""
        if "ltx_appendix" in cls:
            bucket = "appendix"
        else:
            bucket = bucket_for(name.lower())
            if bucket == "other" and name:
                # single-word heuristics the PDF route also leans on
                low = name.lower()
                if "related" in low:
                    bucket = "related"
                elif any(w in low for w in ("method", "approach", "model", "framework")):
                    bucket = "method"
                elif any(w in low for w in ("experiment", "result", "evaluation", "analysis")):
                    bucket = "experiments"
                elif any(w in low for w in ("discussion", "limitation", "conclusion")):
                    bucket = "conclusion"
        if bucket in ("back", "other") and "bib" in (sec.get("id") or ""):
            continue
        body = sec.xpath(".//*[contains(@class,'ltx_para')]")
        txt = _clean(" ".join(p.text_content() for p in body)) if body \
            else _clean(sec.text_content())
        if titles and txt.startswith(_clean(titles[0].text_content())):
            txt = txt[len(_clean(titles[0].text_content())):].strip()
        if len(txt) < 80:
            continue
        # Unlike the PDF route, unrecognised section names are KEPT (as
        # "other"): a method section titled by its system's name ("Any3D-VLA")
        # is the paper's core, not noise. Only back-matter is dropped.
        if bucket != "back":
            sections.append({"bucket": bucket, "title": name or bucket, "text": txt})

    # author block: personnames in document order, each mailto anchor pairs
    # with the personname it follows — the paper's own printed fact, no
    # corresponding-author guess
    authors = []
    for blk in root.xpath(".//div[contains(@class,'ltx_authors')]")[:1]:
        cur = None
        for el in blk.iter():
            cls = el.get("class") or ""
            if "ltx_personname" in cls:
                nm = _clean(el.text_content())
                if nm and (not authors or authors[-1][0] != nm):
                    authors.append([nm, None])
                    cur = len(authors) - 1
            elif (el.tag == "a" and (el.get("href") or "").startswith("mailto:")) \
                    or "ltx_email" in cls:
                em = _clean(el.text_content()).strip("<>")
                if em and "@" in em and "{" not in em and cur is not None \
                        and authors[cur][1] is None:
                    authors[cur][1] = em

    refs = []
    for li in root.xpath(".//li[contains(@class,'ltx_bibitem')]"):
        t = _clean(li.text_content())
        if len(t) > 20:
            refs.append(t[:600])

    return {"sections": sections, "references": refs, "authors": authors, "floats": floats}


MAX_ROWS, MAX_COLS, MAX_CELL = 60, 24, 120


def _floats(root) -> list[dict]:
    """Every captioned figure and table, in document order:
    {kind, id, section, caption, rows}. `rows` (tables only) is the cell text,
    row by row — verbatim strings a benchmark name can be checked against, not
    a reconstruction of the table. Panels of a figure (sub-captions "(a)") are
    folded into their parent's caption list, not listed as floats of their own.
    """
    out = []
    for fig in root.xpath(".//figure"):
        cap = fig.xpath("./figcaption")
        if not cap or fig.xpath("ancestor::figure"):
            continue
        cls = fig.get("class") or ""
        kind = "table" if "ltx_table" in cls else "figure"
        caption = _clean(cap[0].text_content())
        subs = [_clean(c.text_content()) for c in fig.xpath(".//figure/figcaption")]
        subs = [s for s in subs if len(s) > 4]
        sec = fig.xpath("ancestor::section[contains(@class,'ltx_section')"
                        " or contains(@class,'ltx_appendix')][last()]")
        title = sec[0].xpath(".//*[contains(@class,'ltx_title')]") if sec else []
        row = {"kind": kind, "id": fig.get("id"),
               "section": _NUM.sub("", _clean(title[0].text_content())) if title else "",
               "appendix": bool(sec) and "ltx_appendix" in (sec[0].get("class") or ""),
               "caption": caption}
        if subs:
            row["subcaptions"] = subs
        rows = []
        # outer rows only: LaTeXML nests a tabular inside a header cell for
        # line breaks, and its rows would repeat that cell's text as rows
        for tr in fig.xpath(".//tr[not(ancestor::td) and not(ancestor::th)]"):
            cells = [_clean(c.text_content())[:MAX_CELL] for c in tr.xpath("./td|./th")]
            if any(cells):
                rows.append(cells[:MAX_COLS])
            if len(rows) >= MAX_ROWS:
                break
        if rows:
            row["rows"] = rows
        if caption or rows:
            out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--resolved", default=str(RESOLVED))
    ap.add_argument("--ids", default=None, help="event_id subset file")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.8,
                    help="per-worker sleep — arXiv is a shared resource")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--archive", default=None,
                    help="directory for gzipped HTML — cheap insurance against "
                         "the NEXT field we discover we need (decided 2026-08-28)")
    ap.add_argument("--reparse", action="store_true",
                    help="read <archive>/<base>.html.gz when present instead of "
                         "fetching; only papers missing from the archive hit arXiv")
    ap.add_argument("--authors-only", action="store_true",
                    help="harvest the author block only; sections/references "
                         "are not rewritten")
    args = ap.parse_args()
    if args.archive:
        Path(args.archive).mkdir(parents=True, exist_ok=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if out.exists():
        done = {r["arxiv_base"] for r in read_jsonl(out)}

    only = None
    if args.ids:
        only = {int(x) for x in Path(args.ids).read_text().split() if x.strip()}
    targets: dict[str, dict] = {}
    for r in read_jsonl(Path(args.resolved)):
        if r.get("arxiv_base") and (only is None or r["event_id"] in only):
            targets.setdefault(r["arxiv_base"], r)
    todo = [b for b in targets if b not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"resolved={len(targets):,} done={len(done):,} todo={len(todo):,}")
    if not todo:
        return 0

    lock = threading.Lock()
    tally = {"ok": 0, "none": 0, "err": 0, "n": 0, "chars": 0, "refs": 0}
    start = time.time()

    def one(base: str) -> dict:
        row = {"arxiv_base": base}
        cached = Path(args.archive) / f"{base}.html.gz" if args.archive else None
        try:
            if args.reparse and cached and cached.exists():
                return parsed_row(row, gzip.decompress(cached.read_bytes()).decode("utf-8", "replace"))
            text = fetch_html(base)
            if text is None:
                return {**row, "ok": False, "error": "no-html"}
            if cached:
                cached.write_bytes(gzip.compress(text.encode("utf-8"), 6))
            return parsed_row(row, text)
        except Exception as exc:  # noqa: BLE001
            return {**row, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                    "transient": isinstance(exc, (urllib.error.URLError, TimeoutError, OSError))}
        finally:
            if not (args.reparse and cached and cached.exists()):
                time.sleep(args.delay)

    def parsed_row(row: dict, text: str) -> dict:
        parsed = parse_html(text)
        if args.authors_only:
            return {**row, "ok": True, "authors": parsed["authors"]}
        kept = sum(len(s["text"]) for s in parsed["sections"])
        if kept < 500:
            return {**row, "ok": False, "error": f"thin ({kept} chars)"}
        return {**row, "ok": True, **parsed, "chars": kept}

    import concurrent.futures as cf
    with out.open("a", encoding="utf-8") as fh, \
         cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(one, todo):
            # A rate-limited batch must never be recorded as a result
            if row.get("transient"):
                with lock:
                    tally["err"] += 1
                continue
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            with lock:
                tally["n"] += 1
                if row.get("ok"):
                    tally["ok"] += 1
                    tally["chars"] += row.get("chars", 0)
                    tally["refs"] += len(row.get("references") or [])
                else:
                    tally["none"] += 1
                if tally["n"] % 200 == 0:
                    fh.flush()
                    el = time.time() - start
                    print(f"  {tally['n']}/{len(todo)}  ok={tally['ok']} "
                          f"{tally['n']/max(el,1e-6)*3600:.0f}/h", flush=True)

    print(f"\ndone — {tally['ok']} parsed, {tally['none']} unavailable/thin, "
          f"{tally['err']} transient (not recorded)")
    if tally["ok"]:
        print(f"  avg {tally['chars']//tally['ok']:,} chars, "
              f"{tally['refs']/tally['ok']:.0f} references/paper")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
