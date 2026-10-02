"""Benchmark mentions found by rule in a paper's full text — no model.

The first half of the dedicated benchmark pass (CLAUDE.md > Evidence for
benchmark usage). It finds every place a known benchmark name occurs in the
parsed arXiv HTML — prose sentences, figure and table captions, table cells —
and keeps the verbatim sentence or caption beside it.

It also records the role a paper STATES, by rule (see stated_roles): "trains_on"
or "evaluates_on" only when the paper itself is the subject ("we fine-tune …
on X", "our training data", "experiments on X", a "Training data …" or
"Results on …" table). Precision over recall, on the owner's call
(2026-10-02): what the paper states explicitly is enough, and a mention with no
stated role stays "named in the full text" — never "not used".

Vocabulary, two sources:
  * config/benchmarks.json — registered benchmarks; the key is the entry `id`.
  * names the existing extractions recorded for >= MIN_PAPERS papers — not yet
    registered; the key is "k:" + their dataset_key fold (provisional, CLAUDE.md
    > The benchmark is the unit).

A name matches when an n-gram of the text folds (taxonomy.dataset_key) to a
vocabulary key AND either equals a registered name exactly, or is name-shaped:
a digit, a capital after the first letter, or two capitals. That keeps "MATH",
"nuScenes", "CIFAR-10" and refuses "math", "Math", "code", "Once" — the old
extraction recorded common words as dataset names, and their casing proves
nothing. Excluded outright: section numbers ("Appendix C.4" folds to the C4
dataset), model names among the unregistered names (Llama-2-7B, LLaVA-1.5),
and hosts (GitHub, arXiv).

    python3 -m icml.bench_mentions --edition iclr-2025
    python3 -m icml.bench_mentions --all

In:  data/interim/html/fulltext_<edition>.jsonl   (icml.html_extract)
Out: data/interim/mentions/mentions_<edition>.jsonl, one row per parsed paper
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import re
from pathlib import Path

from .common import INTERIM, ROOT, load_json, read_jsonl
from .taxonomy import dataset_key, is_placeholder

HTML_DIR = INTERIM / "html"
OUT_DIR = INTERIM / "mentions"
REGISTRY = ROOT / "config" / "benchmarks.json"
MIN_PAPERS = 5          # an extracted name joins the vocabulary at this many papers
MAX_NGRAM = 5
MAX_EVIDENCE = 3        # verbatim snippets kept per (paper, benchmark)
MAX_ROLE_EVIDENCE = 2   # verbatim snippets kept per (paper, benchmark, stated role)

_SECTION_REF = re.compile(r"^[A-Za-z]\.\d")
_MODEL = re.compile(r"^(llama|qwen|mistral|mixtral|gemma|phi\d|gpt|llava|vicuna|opt\d|pythia|bert|"
                    r"roberta|deberta|t5|flant5|clip|vit|resnet|sam$|sam\d|dino|claude|gemini|deepseek|"
                    r"falcon|bloom|olmo|internvl|blip|whisper|stablediffusion|sdxl|dit\d|unet|opt$|"
                    r"flux|wan\d|hunyuan|cogvideo|sora|kling|pixart|dalle|midjourney|sd\d|svd|"
                    r"animatediff|opensora|lumina|kandinsky|seedream|seedance|veo\d|imagen\d)")
# hosts, and task acronyms the old extraction filed as dataset names
_HOSTS = {"github", "arxiv", "huggingface", "openreview", "kaggle", "paperswithcode", "youtube",
          "ocr", "nli", "asr", "ner", "qa", "abc"}
# a name is letters, digits and a little punctuation; "c=4", "A,B,C" and formula
# text with zero-width spaces are notation that happens to fold onto a name
_NAME_CHARS = re.compile(r"^[A-Za-z0-9τ∞²³ +\-_.'’/&:]+$")
_STOP_START = {"the", "a", "an", "on", "of", "in", "and", "&", "•", "-"}
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")
_EDGE = re.compile(r"^[\s(\[{\"'“‘,;:]+|[\s)\]}\"'”’,;:.]+$")


def vocabulary(min_papers: int = MIN_PAPERS) -> tuple[dict[str, str], dict[str, set[str]], dict[str, str]]:
    """-> (fold -> key, key -> casings seen, key -> display name)."""
    reg = load_json(REGISTRY)
    skip = {dataset_key(s) for s in reg.get("not_a_dataset", [])}
    fold_to: dict[str, str] = {}
    casings: dict[str, set[str]] = collections.defaultdict(set)
    label: dict[str, str] = {}
    for e in reg["benchmarks"]:
        label[e["id"]] = e["name"]
        casings[e["id"]].add(e["name"])          # the only casing that bypasses shape
        for s in [e["name"], *e.get("aliases", [])]:
            k = dataset_key(s)
            if k:
                fold_to.setdefault(k, e["id"])

    papers: collections.Counter = collections.Counter()
    seen: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for f in sorted(glob.glob(str(INTERIM / "facts_abstract*.jsonl"))
                    + glob.glob(str(INTERIM / "facts_fulltext*.jsonl"))):
        for r in read_jsonl(Path(f)):
            keys = set()
            for d in (r.get("facts") or {}).get("datasets") or []:
                n = (d.get("name") or "").strip()
                if not n or is_placeholder(n):
                    continue
                k = dataset_key(n)
                if k and k not in skip:
                    keys.add(k)
                    seen[k][n] += 1
            papers.update(keys)
    for k, n in papers.items():
        if n < min_papers or (len(k) < 3 and not re.search(r"\d", k)):
            continue
        if k not in fold_to and (_MODEL.match(k) or k in _HOSTS):
            continue
        key = fold_to.setdefault(k, "k:" + k)
        label.setdefault(key, seen[k].most_common(1)[0][0])
    return fold_to, casings, label


class Finder:
    def __init__(self, fold_to, casings):
        self.fold_to = fold_to
        self.casings = casings
        # first-token folds that can start a name: rejects most tokens at once
        self.starts = set()
        for k in fold_to:
            self.starts.add(k[:3])

    def _accept(self, gram: str, key: str) -> bool:
        if _SECTION_REF.match(gram) or not _NAME_CHARS.match(gram):
            return False
        if gram in self.casings.get(key, ()):
            return True
        if len(dataset_key(gram)) <= 3:
            # "C3", "c4", "M4": as often a constant or a config as a dataset —
            # only a registered name in its registered casing counts
            return False
        words = gram.split()
        if len(words) > 1 and any(w.isalpha() and w.islower() for w in words[1:]):
            # "3D shapes", "image data": prose. A name written with a generic
            # trailing word still matches through its shorter n-gram.
            return False
        letters = [c for c in gram if c.isalpha()]
        # a digit counts only when it touches a letter: "Text8", "GSM8K",
        # never "text 8" (NeurIPS checklist: "...in the text 8...")
        return (bool(re.search(r"[A-Za-z]\d|\d[A-Za-z]", gram))
                or sum(c.isupper() for c in letters) >= 2
                or any(c.isupper() for c in letters[1:]))

    def find(self, text: str) -> list[tuple[str, str, int]]:
        """-> [(key, surface, token index)] in order, longest match first,
        non-overlapping. Tokens are text.split(), so the index lines up with
        stated_roles()."""
        toks = [_EDGE.sub("", t) for t in text.split()]
        out, i = [], 0
        while i < len(toks):
            hit = None
            head = toks[i]
            if head and head.lower() not in _STOP_START and (
                    dataset_key(head)[:3] in self.starts or len(dataset_key(head)) < 3):
                for n in range(min(MAX_NGRAM, len(toks) - i), 0, -1):
                    gram = " ".join(t for t in toks[i:i + n] if t)
                    if not gram:
                        continue
                    key = self.fold_to.get(dataset_key(gram))
                    if key and self._accept(gram, key):
                        hit = (key, gram, n)
                        break
            if hit:
                out.append((hit[0], hit[1], i))
                i += hit[2]
            else:
                i += 1
        return out


# ---------------------------------------------------------------- stated role
# What the paper SAYS it did with a dataset, read off its own sentence by rule.
# Precision over recall (owner, 2026-10-02: "only what the paper states
# explicitly is enough"): a role needs the paper itself as the subject. Each
# name takes the role of the nearest cue before it in the same sentence; a
# passive cue ("a model trained on X") is someone else's training and blocks.
_FILL = r"(?:(?:also|first|then|further|additionally|only|jointly|directly|separately|subsequently|" \
        r"simply|can|will|could|instead|finally|initially|next|now|again|mainly|primarily|" \
        r"extensively|thoroughly|empirically|systematically)\s+){0,2}"
_TRAIN_VERB = r"(?:pre-?train|train|fine-?tune|finetune|instruction-?tune|post-?train|distill|" \
              r"continually pre-?train|continue to pre-?train)\w*"
_EVAL_VERB = r"(?:evaluat|test|benchmark|assess|validat|report|conduct\w* (?:\w+ )?experiments|" \
             r"experiment|compar)\w*"
_CUES = [
    ("trains_on", re.compile(rf"\bwe {_FILL}{_TRAIN_VERB}\b", re.I)),
    ("trains_on", re.compile(r"\bour (?:own )?(?:pre-?training|training|fine-?tuning|finetuning|sft|"
                             r"instruction[- ]tuning|post-?training) (?:data|dataset|datasets|set|sets|"
                             r"corpus|corpora|mixture|split)s?\b", re.I)),
    ("trains_on", re.compile(r"\bwe use (?:\S+ ){0,8}?(?:for|as) (?:the |our )?(?:pre-?training|training|"
                             r"fine-?tuning|finetuning)\b", re.I)),
    ("evaluates_on", re.compile(rf"\bwe {_FILL}{_EVAL_VERB}\b", re.I)),
    ("evaluates_on", re.compile(r"\bour (?:evaluation|test|benchmark)s? (?:data|datasets?|benchmarks?|"
                                r"suite|sets?)\b", re.I)),
    ("evaluates_on", re.compile(r"\b(?:experiments|results|evaluations?) (?:are |were )?(?:conducted |"
                                r"performed |reported )?(?:on|across)\b", re.I)),
    (None, re.compile(r"\b(?:pre-?trained|trained|fine-?tuned|finetuned|distilled) (?:\S+ ){0,3}?"
                      r"(?:on|with|using|from)\b", re.I)),
]
# a second verb joined to a first-person one ("We train on C4 and evaluate on
# GSM8K") — only counted in a sentence that already has the paper as subject
_COORD = [
    ("trains_on", re.compile(rf"\b(?:and|then)\s+(?:then\s+)?{_TRAIN_VERB}\b", re.I)),
    ("evaluates_on", re.compile(rf"\b(?:and|then)\s+(?:then\s+)?{_EVAL_VERB}\b", re.I)),
]
_FIRST_PERSON = re.compile(r"\b(?:we|our)\b", re.I)
_TRAIN_CAPTION = re.compile(r"\b(?:pre-?training|training|fine-?tuning|finetuning|sft|instruction[- ]tuning)"
                            r" (?:data|dataset|datasets|set|sets|corpus|corpora|mixture)", re.I)
_EVAL_CAPTION = re.compile(r"^(?:table \S+ )?(?:main )?results\b|\b(?:results|accuracy|performance|"
                           r"comparison|evaluation|scores?) (?:on|across)\b", re.I)
_NO_ROLE_BUCKETS = {"related", "background", "references", "back"}


def stated_roles(sentence: str) -> list[tuple[int, str | None]]:
    """-> [(token index, role or None)] for every cue, in order."""
    cues = []
    for role, rx in _CUES + (_COORD if _FIRST_PERSON.search(sentence) else []):
        for m in rx.finditer(sentence):
            cues.append((len(sentence[:m.start()].split()), role, m.start()))
    # where two cues start at the same token, the first-person one wins
    cues.sort(key=lambda c: (c[0], c[1] is None))
    return [(i, r) for i, r, _ in cues]


def role_at(cues: list[tuple[int, str | None]], token: int) -> str | None:
    role = None
    for i, r in cues:
        if i > token:
            break
        role = r
    return role


_SURVEY_CAPTION = re.compile(r"\b(?:comparison|compared|existing|prior|previous|related|versus|vs\.?|"
                             r"overview of (?:datasets|benchmarks)|statistics)\b", re.I)


def caption_role(caption: str) -> str | None:
    if _SURVEY_CAPTION.search(caption) and not re.search(r"\b(?:results|accuracy|performance)\b",
                                                            caption, re.I):
        return None     # a table about datasets in general, not what this paper used
    if _TRAIN_CAPTION.search(caption):
        return "trains_on"
    if _EVAL_CAPTION.search(caption):
        return "evaluates_on"
    return None


def mentions_of(row: dict, finder: Finder) -> dict:
    count: collections.Counter = collections.Counter()
    where: dict[str, set[str]] = collections.defaultdict(set)
    surface: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    ev: dict[str, list[dict]] = collections.defaultdict(list)
    roles: dict[str, dict[str, list[dict]]] = collections.defaultdict(lambda: collections.defaultdict(list))

    def note(key, surf, place, text, role=None):
        count[key] += 1
        where[key].add(place)
        surface[key][surf] += 1
        if len(ev[key]) < MAX_EVIDENCE:
            ev[key].append({"where": place, "text": text[:500]})
        if role and len(roles[key][role]) < MAX_ROLE_EVIDENCE:
            roles[key][role].append({"where": place, "text": text[:500]})

    for sec in row.get("sections") or []:
        if "checklist" in (sec.get("title") or "").lower():
            continue        # NeurIPS's mandatory checklist: boilerplate, not the paper
        for sent in _SENT.split(sec["text"]):
            found = finder.find(sent)
            if not found:
                continue
            cues = [] if sec["bucket"] in _NO_ROLE_BUCKETS else stated_roles(sent)
            for key, surf, tok in found:
                note(key, surf, sec["bucket"], sent, role_at(cues, tok))
    for fl in row.get("floats") or []:
        place = f"{fl['kind']}{'@appendix' if fl.get('appendix') else ''}"
        cap = fl.get("caption") or ""
        crole = caption_role(cap)
        found = set()
        for key, surf, _ in finder.find(cap):
            note(key, surf, place + ":caption", cap, crole)
            found.add(key)
        for r in fl.get("rows") or []:
            for cell in r:
                for key, surf, _ in finder.find(cell):
                    if key not in found:     # one table names a benchmark once
                        found.add(key)
                        note(key, surf, place + ":cell",
                             f"{fl.get('id') or ''} | {cap[:200]} | cell: {cell}", crole)
    return {"arxiv_base": row["arxiv_base"],
            "mentions": {k: {"n": count[k], "where": sorted(where[k]),
                             "as_written": surface[k].most_common(1)[0][0], "evidence": ev[k],
                             **({"roles": {r: v for r, v in roles[k].items()}} if roles.get(k) else {})}
                         for k in count}}


def run(edition: str, finder: Finder) -> tuple[int, int]:
    src = HTML_DIR / f"fulltext_{edition}.jsonl"
    if not src.exists():
        print(f"[{edition}] no {src.name} — run icml.html_extract first")
        return 0, 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"mentions_{edition}.jsonl"
    n = named = 0
    with out.with_suffix(".tmp").open("w", encoding="utf-8") as fh:
        for row in read_jsonl(src):
            if not row.get("ok"):
                continue
            m = mentions_of(row, finder)
            n += 1
            named += bool(m["mentions"])
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    out.with_suffix(".tmp").replace(out)
    print(f"[{edition}] {n} parsed papers, {named} name >= 1 benchmark -> {out.name}")
    return n, named


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--edition", action="append", default=[], help="e.g. iclr-2025 (repeatable)")
    ap.add_argument("--all", action="store_true", help="every edition with parsed HTML")
    args = ap.parse_args()
    fold_to, casings, label = vocabulary()
    print(f"vocabulary: {len(set(fold_to.values()))} benchmarks, {len(fold_to)} folded surfaces")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "vocabulary.json").write_text(json.dumps(
        {"min_papers": MIN_PAPERS, "label": label}, ensure_ascii=False, indent=1), encoding="utf-8")
    finder = Finder(fold_to, casings)
    eds = args.edition or ([p.stem.removeprefix("fulltext_") for p in sorted(HTML_DIR.glob("fulltext_*.jsonl"))]
                           if args.all else [])
    if not eds:
        ap.error("give --edition or --all")
    for e in eds:
        run(e, finder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
