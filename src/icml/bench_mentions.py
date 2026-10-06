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
import datetime as dt
import glob
import json
import re
from pathlib import Path

from .common import INTERIM, ROOT, dump_json, load_json, read_jsonl
from .taxonomy import dataset_key, is_placeholder

HTML_DIR = INTERIM / "html"
OUT_DIR = INTERIM / "mentions"
REGISTRY = ROOT / "config" / "benchmarks.json"
INTRODUCED = ROOT / "config" / "benchmarks_introduced.json"
MIN_PAPERS = 5          # an extracted name joins the vocabulary at this many papers
MAX_NGRAM = 5
MAX_EVIDENCE = 3        # verbatim snippets kept per (paper, benchmark)
MAX_ROLE_EVIDENCE = 2   # verbatim snippets kept per (paper, benchmark, stated role)

_DATA_WORD = re.compile(r"\b(?:datasets?|benchmarks?|corpus|corpora|data)\b", re.I)
AMBIGUOUS: set[str] = set()   # registry ids that are also ordinary words (filled by vocabulary())
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


def _plain_word(name: str) -> bool:
    """One capitalized word with no digit or inner capital — also an English word."""
    return (" " not in name and not re.search(r"\d", name)
            and not any(c.isupper() for c in name[1:]))


def vocabulary(min_papers: int = MIN_PAPERS) -> tuple[dict[str, str], dict[str, set[str]], dict[str, str]]:
    """-> (fold -> key, key -> casings seen, key -> display name)."""
    reg = load_json(REGISTRY)
    skip = {dataset_key(s) for s in reg.get("not_a_dataset", [])}
    fold_to: dict[str, str] = {}
    casings: dict[str, set[str]] = collections.defaultdict(set)
    label: dict[str, str] = {}
    for e in reg["benchmarks"]:
        if e.get("ambiguous"):
            AMBIGUOUS.add(e["id"])
        label[e["id"]] = e["name"]
        casings[e["id"]].add(e["name"])          # the only casing that bypasses shape
        for s in [e["name"], *e.get("aliases", [])]:
            k = dataset_key(s)
            if k:
                fold_to.setdefault(k, e["id"])

    # benchmarks a paper in the corpus says it built (icml.bench_mentions --introduced)
    # only claims Claude Code's review confirmed; same-named products share one
    # key here and are told apart later by what the role sentence cites
    if INTRODUCED.exists():
        for e in load_json(INTRODUCED)["benchmarks"]:
            if e.get("registered") or e["fold"] in fold_to:
                continue
            key = e["id"] if not e.get("homonym_of") else "hom:" + e["fold"]
            fold_to[e["fold"]] = key
            casings[key].add(e["name"])
            label[key] = e["name"]
            if _plain_word(e["name"]) or not _strong_name(e["name"]) or len(e["fold"]) <= 4:
                # "Waterbirds", "Scene Graph", "RGB", "SAM": also prose, a color
                # space or a model, so a stated role with an attached data word
                # is required
                AMBIGUOUS.add(key)

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
        if key in AMBIGUOUS:
            return False        # only its registered casing
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

    def find(self, text: str) -> list[tuple[str, str, int, int]]:
        """-> [(key, surface, token index, tokens spanned)] in order, longest match first,
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
                out.append((hit[0], hit[1], i, hit[2]))
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
_CUE_HINT = re.compile(r"\b(?:we|our|experiments|results|evaluations?)\b", re.I)
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


# ---------------------------------------------------------------- unlisted
# Names outside the vocabulary — a new benchmark, a paper's own data — found
# where the paper states a role: the name-shaped phrase after the cue's
# preposition ("we evaluate on Stanford-ORB, Objects-with-Lighting and ...").
# Kept per paper with its sentence; recurring ones are reviewed into the
# registry (python3 -m icml.bench_mentions --candidates).
# "with"/"using" are not training anchors: "we train with Adam" names an optimizer
_ANCHORS = {"evaluates_on": {"on", "across", "over"},
            "trains_on": {"on", "from", "including"}}
# what the first review of the unlisted list (ICLR 2025, 2026-10-02) showed
# recurring after those anchors without being data: hardware and software,
# optimizers, training methods, metrics, model families, generic acronyms, sizes
_NOT_DATA = re.compile(
    r"\b(?:gpus?|cpus?|tpus?|nvidia|geforce|a100|h100|h800|a800|v100|a6000|rtx|\d+gb|linux|ubuntu|"
    r"pytorch|jax|tensorflow|cuda|openai|adam|adamw|sgd|lora|qlora|dpo|ppo|kto|grpo|sft|rlhf|cot|icl|"
    r"sac|td3|dqn|ddim|ddpm|mse|ssim|psnr|lpips|iou|fid|bleu|rouge|auc|f1|accuracy|mlp|cnn|gnn|gcn|"
    r"rnn|lstm|transformers?|mamba|gaussian|relu|pca|sota|ood|nlp|rgb|ssl|zero-shot|few-shot|"
    r"low-rank adaptation|supervised fine-tuning|first|english|aupr|auroc|fpr95|miou|union|"
    r"llm-as-a-judge|np-complete|ntk)\b", re.I)
_SIZE = re.compile(r"^(?:\d+(?:\.\d+)?[kmbt]?|v\d+(?:\.\d+)*|\d+(?:\.\d+)?e-?\d+|\d+\s*x\s*\d+)$", re.I)
# cues too loose to name data that is not already known: comparing methods, and
# "results/evaluation on" in third person
_LOOSE_CUE = re.compile(r"^(?:\S+\s+){0,3}?(?:compar|against|results|evaluations?\b)", re.I)
_PLURAL_ACRONYM = re.compile(r"^[A-Z]{2,}s$")
_SKIP = {"the", "a", "an", "and", "or", "&", "as", "well", "both", "several", "multiple", "two",
         "three", "four", "five", "six", "seven", "eight", "nine", "ten", "standard", "popular",
         "public", "publicly", "available", "widely", "used", "following", "benchmark",
         "benchmarks", "dataset", "datasets", "suite", "suites", "task", "tasks", "set", "sets",
         "split", "splits", "of", "respectively", "e.g.", "i.e.", "such", "like"}
_NOT_NAMES = {"we", "our", "the", "this", "these", "those", "table", "tables", "figure", "fig",
              "section", "sec", "appendix", "eq", "equation", "all", "each", "both", "it", "its",
              "their", "in", "for", "to", "average", "avg", "overall", "results", "main"}


def _name_like(t: str) -> bool:
    if not t or t.lower() in _NOT_NAMES or not _NAME_CHARS.match(t) or not any(c.isalpha() for c in t):
        return False
    return t[0].isupper() or bool(re.search(r"[A-Za-z]\d|\d[A-Za-z]", t))


def unlisted_names(sentence: str, cues, covered: set[int], known) -> list[tuple[str, str, str]]:
    """-> [(fold, as written, role)] for names after a role cue's preposition."""
    raw = sentence.split()
    toks = [_EDGE.sub("", t) for t in raw]
    out = []
    for c, role in cues:
        if role is None or _LOOSE_CUE.match(" ".join(toks[c:c + 4])):
            continue
        anchor = next((j for j in range(c + 1, min(c + 14, len(toks)))
                       if toks[j].lower() in _ANCHORS[role] or raw[j].endswith(":")), None)
        if anchor is None:
            continue
        k, skipped, depth = anchor + 1, 0, 0
        while k < len(toks) and k < anchor + 30 and skipped <= 4:
            r = raw[k]
            if depth or r.startswith(("(", "[")):          # a citation or a gloss
                depth += r.count("(") + r.count("[") - r.count(")") - r.count("]")
                depth = max(depth, 0)
                k += 1
                continue
            if k in covered:                                # a known name: keep listing
                k += 1
                continue
            if _name_like(toks[k]) and not (k + 1 < len(toks) and toks[k + 1].lower() == "et"):
                run = [toks[k]]
                while (k + len(run) < len(toks) and len(run) < 4 and not raw[k + len(run) - 1].endswith((",", ";"))
                       and (k + len(run)) not in covered and toks[k + len(run)][:1].isupper()
                       and not raw[k + len(run)].startswith(("(", "["))
                       and _name_like(toks[k + len(run)])):
                    run.append(toks[k + len(run)])
                name = " ".join(run)
                fold = dataset_key(name)
                single_word = len(run) == 1 and not re.search(r"\d", name) and sum(ch.isupper() for ch in name) < 2
                if (fold and len(fold) >= 3 and fold not in known and not _MODEL.match(fold)
                        and fold not in _HOSTS and not (single_word and len(name) < 4)
                        and not _NOT_DATA.search(name) and not _SIZE.match(name)
                        and not _PLURAL_ACRONYM.match(name)):
                    out.append((fold, name, role))
                k += len(run)
                skipped = 0
                continue
            if toks[k].lower() in _SKIP or not toks[k]:
                skipped += 1
                k += 1
                continue
            break                                            # prose resumes
    return out


# ---------------------------------------------------------------- introduced
# A benchmark the paper says it built. Registered whatever its frequency
# (owner, 2026-10-06): a new benchmark starts with one paper. The claim needs
# the paper as subject and the name joined to a data noun — "we introduce X,
# a new benchmark ..." or "we present a new benchmark, X" — so "we propose X,
# a framework for benchmark ..." (a method) does not qualify.
_INTRO_VERB = r"(?:introduc|present|propos|construct|buil[dt]|creat|curat|collect|releas|develop|" \
              r"contribut|design|establish|assembl|compil)\w*"
_BUILD_VERB = r"(?:introduc|present|propos|construct|buil[dt]|creat|releas|develop|design|establish)\w*"
_INTRO_SUBJ = rf"(?:\bwe\s+(?:have\s+|had\s+)?{_FILL}{_INTRO_VERB}|\bthis\s+(?:paper|work)\s+{_INTRO_VERB}|" \
              rf"(?P<coord>\b(?:and|then)\s+(?:also\s+)?{_INTRO_VERB}))"
_INTRO_NOUN = r"(?:benchmark|dataset|data\s?set|suite|test-?bed|corpus|corpora|evaluation\s+(?:suite|set)|" \
              r"environment|challenge)s?"
_NUMERAL = r"(?:two|three|four|five|six|seven|eight|nine|ten|\d+)"
_INTRO_NAME = r"(?P<name>[^\s,:;()]+(?:\s+[A-Z0-9][^\s,:;()]*){0,4})"
_INTRO_ACRO = r"(?:\s*\((?P<acro>[^()\s,;]{2,24})\))?"
_INTRO_MID = r"(?P<mid>(?:[\w-]+,?\s+){0,7}?)"
_INTRO = [
    re.compile(rf"{_INTRO_SUBJ}\s+(?:the\s+)?{_INTRO_NAME}{_INTRO_ACRO}\s*(?:,|:|—|–)\s*(?:which\s+is\s+)?"
               rf"(?:a|an|the|our)?\s*{_INTRO_MID}(?P<noun>{_INTRO_NOUN})\b", re.I),
    # "we have developed the MR-GSM8K benchmark", "the Multimodal Multi-image
    # Understanding (MMIU) benchmark"
    # "we build a new benchmark based on existing datasets, called MixBench"
    re.compile(rf"{_INTRO_SUBJ}\s+(?:a|an|the|our)\s+{_INTRO_MID}(?P<noun>{_INTRO_NOUN})\b[^.;:()]{{1,70}}?"
               rf"\b(?:called|named|dubbed|termed|coined)\s+{_INTRO_NAME}{_INTRO_ACRO}", re.I),
    # (gathering verbs excluded here: "we collect the Cora dataset" downloads it)
    re.compile(rf"{_INTRO_SUBJ.replace(_INTRO_VERB, _BUILD_VERB)}\s+(?:the|a|an|our)\s+(?:new\s+|novel\s+)?"
               rf"{_INTRO_NAME}{_INTRO_ACRO}\s+(?P<mid>)(?P<noun>{_INTRO_NOUN})\b", re.I),
    re.compile(rf"{_INTRO_SUBJ}\s+(?:a|an|the|our|{_NUMERAL})\s+{_INTRO_MID}(?P<noun>{_INTRO_NOUN})\s*"
               rf"(?:,\s*|\(\s*|:\s*|\s+(?:called|named|dubbed|termed|coined|namely)\s+|\s+)"
               rf"{_INTRO_NAME}{_INTRO_ACRO}", re.I),
]
# the data noun must head the verb's object. A preposition in the phrase before
# it ("we construct the attack input FROM three jailbreak benchmarks (HarmBench,
# ...)") puts the noun inside what was used, not what was built; another head
# noun ("a framework for benchmark ...") means the name labels something else.
# Words like "existing" or "three" are not grounds: "three new benchmarks" and
# "a new benchmark based on existing data" are claims.
_INTRO_MID_BAD = re.compile(r"\b(?:for|to|of|on|in|with|from|by|via|at|into|over|across|through|"
                            r"that|which|framework|method|model|approach|"
                            r"algorithm|system|pipeline|architecture|technique|agent|tool|toolkit|library|"
                            r"metric|protocol|loss|strategy|module|network|paradigm)\b", re.I)


def _acronym_of(acro: str, phrase: str) -> bool:
    """Do the acronym's capitals follow the initials of the phrase's words, in
    order? "RL-ViGen" ~ "Reinforcement Learning benchmark for Visual
    Generalization"; "SRB" ~ "Speech Robust Bench"."""
    caps = [c for c in acro if c.isupper()]
    if len(caps) < 2:
        return False
    initials = [w[0].upper() for w in re.findall(r"[A-Za-z][\w']*", phrase)]
    i = 0
    for ch in caps:
        while i < len(initials) and initials[i] != ch:
            i += 1
        if i == len(initials):
            return False
        i += 1
    return True


class _Unique(list):
    """A list that ignores a name it already holds (two claim shapes can match one claim)."""
    def append(self, item):
        if dataset_key(item[0]) not in {dataset_key(x[0]) for x in self}:
            super().append(item)


def introduced_names(sentence: str) -> list[tuple[str, str]]:
    """-> [(name as written, data noun)] the sentence says the paper built."""
    out = _Unique()
    for rx in _INTRO:
        for m in rx.finditer(sentence):
            if _INTRO_MID_BAD.search(m.group("mid") or ""):
                continue
            if m.group("coord") and not _FIRST_PERSON.search(sentence[:m.start()]):
                continue            # "... and construct X" needs a "we" before it
            acro = (m.groupdict().get("acro") or "").strip()
            if not acro:
                # "a novel Reinforcement Learning benchmark for Visual Generalization
                # (RL-ViGen)": an acronym later in the same clause names the phrase
                later = re.match(r"[^.;()]{0,90}?\((?P<a>[^()\s,;]{2,24})\)", sentence[m.end():])
                if later and _acronym_of(later.group("a"),
                                         sentence[m.start("name"):m.end() + later.start("a")]):
                    acro = later.group("a")
            if acro and _name_like(acro) and _acronym_of(acro, m.group("name") + " " +
                                                         sentence[m.end("name"):m.end()] + " " +
                                                         sentence[m.end():m.end() + 90]):
                name, run = acro, [acro]     # "Speech Robust Bench (SRB)" -> SRB
            else:
                words = [_EDGE.sub("", w) for w in m.group("name").split()]
                run = []
                for w in words:             # keep the leading name-shaped run
                    if not w or not (_name_like(w) or (run and (w[:1].isupper() or w[:1].isdigit()))):
                        break
                    run.append(w)
                if run and run[-1].lower() in _INTRO_GENERIC_TAIL:
                    run = run[:-1]          # "... RL Benchmark" -> the name before the noun
                name = " ".join(run)
            fold = dataset_key(name)
            if (not run or len(fold) < 3 or _NOT_DATA.search(name) or _MODEL.match(fold)
                    or re.match(r"^\d+(?:\.\d+)?[kmbt]?-", name, re.I)
                    or fold in _INTRO_NOT_NAME or _INTRO_REF.match(name)
                    or re.match(rf"\s*(?:\[\d|et\s+al)", sentence[m.end("name"):m.end("name") + 8])
                    or _SIZE.match(name) or fold in _HOSTS or name.lower() in _INTRO_GENERIC):
                continue
            noun = re.sub(r"\s+", " ", m.group("noun").lower())
            out.append((name, noun))
            if noun.endswith("s"):
                # "three new benchmarks, CodeA, CodeB and CodeC": the rest of the list
                tail = re.match(r"((?:\s*(?:,|and|,\s*and)\s*[A-Za-z0-9][^\s,;:()]*(?:\s*\([^()]*\))?)+)",
                                sentence[m.end("name"):m.end("name") + 200])
                for nxt in re.findall(r"(?:,|\band\b)\s*([A-Za-z0-9][^\s,;:()]*)", tail.group(1) if tail else ""):
                    nxt = _EDGE.sub("", nxt)
                    if _name_like(nxt) and _strong_name(nxt) and dataset_key(nxt) not in _INTRO_NOT_NAME:
                        out.append((nxt, noun))
    return out


_INTRO_HINT = re.compile(r"\b(?:we|this (?:paper|work))\b.*\b(?:benchmark|dataset|data set|suite|"
                         r"test-?bed|corpus|corpora|environment|challenge)", re.I)
_INTRO_GENERIC_TAIL = {"benchmark", "benchmarks", "dataset", "datasets", "suite", "corpus"}
_INTRO_GENERIC = {"a", "an", "the", "new", "novel", "this", "our", "benchmark", "dataset", "it", "them"}
# what the first full run (2026-10-06) showed the claim rule capturing that is
# not a name: generic model/task acronyms, table and figure references, and
# plain words. Compared on the dataset_key fold.
_INTRO_NOT_NAME = {
    "llm", "llms", "mllm", "mllms", "vlm", "vlms", "lvlm", "lvlms", "lmm", "lmms", "api", "apis", "rag",
    "vqa", "t2i", "t2v", "i2v", "roc", "lidar", "nlp", "gpu", "gpus", "ai", "ml", "rl", "cv", "qa", "llmbased",
    "evaluation", "image", "images", "human", "humans", "object", "objects", "synthetic", "generation",
    "train", "training", "test", "text", "video", "videos", "audio", "model", "models", "data", "task",
    "tasks", "ours", "real", "realworld", "simulation", "experiments", "results", "appendix", "section",
    "llmgenerated", "aigenerated", "humanannotated"}
_INTRO_REF = re.compile(r"^(?:tab|table|fig|figure|sec|section|appendix|eq|equation|alg|algorithm)\.?\b", re.I)


# no bare "data": "RGB data", "SAM data" attach the word to anything
_DATA_NOUN = {"dataset", "datasets", "benchmark", "benchmarks", "corpus", "corpora", "suite",
              "suites", "environment", "environments", "testbed", "testbeds"}
_LIST_GLUE = {"and", "or", "&", "including", "include", "includes", "such", "as", "namely", "like",
              "the", "e.g.", "i.e.", "viz.", "respectively"}


def data_linked(sentence: str, tok: int, n: int, covered: set[int]) -> bool:
    """Is a data word attached to the name at tokens [tok, tok+n)? Either right
    after it ("the CLEAR dataset", "AMOS benchmark") or heading the list it sits
    in ("on four datasets, including PANORAMA [2], AMOS [23], FeTA"). A data word
    elsewhere in the sentence does not count: in "We compare CLEAR with other
    methods on the CIFAR-10 dataset" the dataset is CIFAR-10."""
    raw = sentence.split()
    clean = [_EDGE.sub("", t) for t in raw]
    toks = [t.lower() for t in clean]

    def member(j: int) -> bool:          # another name in the same list
        return j in covered or (_name_like(clean[j]) and toks[j] not in _DATA_NOUN)

    k = tok + n                                                # "FLAIR, a land-cover dataset"
    if raw[k - 1].endswith(",") and k < len(toks) and toks[k] in ("a", "an", "the"):
        if any(t in _DATA_NOUN for t in toks[k + 1: k + 5]):
            return True
    for j in range(tok + n, min(tok + n + 8, len(toks))):      # "Synapse and ACDC datasets"
        if toks[j] in _DATA_NOUN:
            return True
        if not (member(j) or toks[j] in _LIST_GLUE) or raw[j - 1].endswith((".", ";")):
            break
    depth = 0
    for j in range(tok - 1, max(tok - 16, -1), -1):
        r, t = raw[j], toks[j]
        depth += r.count(")") + r.count("]") - r.count("(") - r.count("[")
        if depth > 0 or r.startswith(("(", "[")) or r.endswith((")", "]", "),", "],")):
            depth = max(depth, 0)
            continue                                   # a citation or a gloss
        if member(j) or not t or t in _LIST_GLUE:
            continue                                   # another name, or list glue
        return t.rstrip(":") in _DATA_NOUN             # the list's head word, or prose
    return False


def citation_resolver(row: dict):
    """sentence -> the reference texts it cites, from the parser's `cites` map.
    Numeric citations ("12") count only inside brackets, so a bare number in
    prose is not a citation."""
    refs = dict(zip(row.get("ref_ids") or [], row.get("references") or []))
    surfaces = [(s_, ids) for s_, ids in (row.get("cites") or {}).items() if s_]
    numeric = [(s_, ids) for s_, ids in surfaces if s_.isdigit()]
    named = [(s_, ids) for s_, ids in surfaces if not s_.isdigit()]

    def cited(sentence: str) -> set[str]:
        out = set()
        for s_, ids in named:
            if s_ in sentence:
                out.update(refs.get(i, "")[:200] for i in ids)
        if numeric and "[" in sentence:
            groups = " ".join(re.findall(r"\[([^\]]{1,60})\]", sentence))
            for s_, ids in numeric:
                if re.search(rf"\b{s_}\b", groups):
                    out.update(refs.get(i, "")[:200] for i in ids)
        out.discard("")
        return out
    return cited


def mentions_of(row: dict, finder: Finder) -> dict:
    count: collections.Counter = collections.Counter()
    where: dict[str, set[str]] = collections.defaultdict(set)
    surface: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    ev: dict[str, list[dict]] = collections.defaultdict(list)
    roles: dict[str, dict[str, list[dict]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    unlisted: dict[str, dict] = {}
    introduces: dict[str, dict] = {}

    cited = citation_resolver(row)
    role_refs: dict[str, set[str]] = collections.defaultdict(set)

    def note(key, surf, place, text, role=None, linked=None):
        if key in AMBIGUOUS and (role is None or not (
                linked if linked is not None else _DATA_WORD.search(text))):
            # an ambiguous name counts only where a role is stated AND a data
            # word is attached to it (sentences) or heads the table (floats)
            return
        count[key] += 1
        where[key].add(place)
        surface[key][surf] += 1
        if len(ev[key]) < MAX_EVIDENCE:
            ev[key].append({"where": place, "text": text[:500]})
        if role and len(roles[key][role]) < MAX_ROLE_EVIDENCE:
            roles[key][role].append({"where": place, "text": text[:500]})
        if role and ":" not in place:
            # what the role sentence cites: which of two same-named products it means
            role_refs[key].update(cited(text))

    for sec in row.get("sections") or []:
        if "checklist" in (sec.get("title") or "").lower():
            continue        # NeurIPS's mandatory checklist: boilerplate, not the paper
        for sent in _SENT.split(sec["text"]):
            found = finder.find(sent)
            cues = [] if sec["bucket"] in _NO_ROLE_BUCKETS or not _CUE_HINT.search(sent) \
                else stated_roles(sent)
            if sec["bucket"] not in _NO_ROLE_BUCKETS and _INTRO_HINT.search(sent):
                for name, noun in introduced_names(sent):
                    fold = dataset_key(name)
                    key = finder.fold_to.get(fold) or "new:" + fold
                    it = introduces.setdefault(key, {"name": name, "noun": noun, "evidence": []})
                    if len(it["evidence"]) < MAX_ROLE_EVIDENCE:
                        it["evidence"].append({"where": sec["bucket"], "text": sent[:500]})
            if not found and not cues:
                continue
            covered = {i for _, _, tok, n in found for i in range(tok, tok + n)}
            for key, surf, tok, n in found:
                note(key, surf, sec["bucket"], sent, role_at(cues, tok),
                     data_linked(sent, tok, n, covered) if key in AMBIGUOUS else None)
            for fold, name, role in unlisted_names(sent, cues, covered, finder.fold_to):
                u = unlisted.setdefault(fold, {"as_written": name, "roles": [], "evidence": []})
                if role not in u["roles"]:
                    u["roles"].append(role)
                if len(u["evidence"]) < MAX_ROLE_EVIDENCE:
                    u["evidence"].append({"where": sec["bucket"], "role": role, "text": sent[:500]})
    for fl in row.get("floats") or []:
        place = f"{fl['kind']}{'@appendix' if fl.get('appendix') else ''}"
        cap = fl.get("caption") or ""
        crole = caption_role(cap)
        found = set()
        for key, surf, *_ in finder.find(cap):
            note(key, surf, place + ":caption", cap, crole)
            found.add(key)
        for r in fl.get("rows") or []:
            for cell in r:
                for key, surf, *_ in finder.find(cell):
                    if key not in found:     # one table names a benchmark once
                        found.add(key)
                        note(key, surf, place + ":cell",
                             f"{fl.get('id') or ''} | {cap[:200]} | cell: {cell}", crole)
    return {"arxiv_base": row["arxiv_base"],
            "mentions": {k: {"n": count[k], "where": sorted(where[k]),
                             "as_written": surface[k].most_common(1)[0][0],
                             # every spelling kept: "val2017" says which COCO 2017 split
                             "surfaces": dict(surface[k].most_common(6)), "evidence": ev[k],
                             **({"roles": {r: v for r, v in roles[k].items()}} if roles.get(k) else {}),
                             **({"role_cites": sorted(role_refs[k])[:12]} if role_refs.get(k) else {})}
                         for k in count},
            **({"unlisted": unlisted} if unlisted else {}),
            **({"introduces": introduces} if introduces else {})}


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


def candidates(min_papers: int = 3) -> Path:
    """Unlisted names across every edition -> one review list, most papers first."""
    agg: dict[str, dict] = {}
    for f in sorted(OUT_DIR.glob("mentions_*.jsonl")):
        ed = f.stem.removeprefix("mentions_")
        for r in read_jsonl(f):
            for fold, u in (r.get("unlisted") or {}).items():
                a = agg.setdefault(fold, {"papers": 0, "editions": collections.Counter(),
                                          "roles": collections.Counter(),
                                          "as_written": collections.Counter(), "examples": []})
                a["papers"] += 1
                a["editions"][ed] += 1
                a["roles"].update(u["roles"])
                a["as_written"][u["as_written"]] += 1
                if len(a["examples"]) < 2:
                    a["examples"].append(u["evidence"][0]["text"][:300])
    rows = [{"fold": k, "name": a["as_written"].most_common(1)[0][0], "papers": a["papers"],
             "editions": len(a["editions"]), "roles": dict(a["roles"]), "examples": a["examples"]}
            for k, a in agg.items() if a["papers"] >= min_papers]
    rows.sort(key=lambda r: -r["papers"])
    out = OUT_DIR / "unlisted_candidates.json"
    out.write_text(json.dumps({"min_papers": min_papers, "names": len(agg), "listed": len(rows),
                               "candidates": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"unlisted: {len(agg)} distinct names, {len(rows)} in >= {min_papers} papers -> {out.name}")
    return out


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


def _slug(name: str) -> str:
    s = name.replace("τ", "tau").replace("²", "2").replace("∞", "inf").replace("+", "-plus").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _strong_name(name: str) -> bool:
    letters = [c for c in name if c.isalpha()]
    return (sum(c.isupper() for c in letters) >= 2 or bool(re.search(r"[A-Za-z]\d|\d[A-Za-z]", name))
            or any(c.isupper() for c in letters[1:]))


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
    n = 0
    for line in open(path, encoding="utf-8"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 2 or parts[1] not in LABELS:
            continue
        rev["claims"][parts[0]] = {"label": parts[1], "status": LABELS[parts[1]][0],
                                   **({"note": parts[2]} if len(parts) > 2 and parts[2] else {}),
                                   "reviewed": dt.date.today().isoformat()}
        n += 1
    dump_json(REVIEW, rev)
    print(f"review: {n} decisions recorded, {len(rev['claims'])} in total -> {REVIEW.name}")


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
    for c in claims:
        c["review"] = rev["claims"].get(c["claim_id"])
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
        if c["review"] and m and c["review"]["label"] in ("X", "E", "T"):
            c["name"], c["fold"] = m.group(1).strip(), dataset_key(m.group(1))
            if c["review"]["label"] == "X":
                kind = m.group(2) or ("E" if re.search(r"bench|suite|test|evaluat", c["noun"]) else "T")
                c["review"] = {**c["review"], "status": "confirmed", "label": kind, "renamed_from_X": True}
            # renamed onto a registered name: that benchmark's own paper only if
            # its title names it ("FSD" in the Bench2Drive paper); otherwise the
            # correction points at data the paper used, and nothing was introduced
            c["registered"] = reg_fold.get(c["fold"])
            if c["registered"]:
                if _title_names(c["name"], c["title"]):
                    c["review"] = {**c["review"], "note": "same"}
                else:
                    c["review"] = {**c["review"], "status": "excluded", "label": "U"}
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
        people = {_person(a) for a in c["authors"]}
        for g in groups[c["fold"]]:
            if people & {_person(a) for x in g for a in x["authors"]}:
                g.append(c)
                break
        else:
            groups[c["fold"]].append([c])
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
                "first_claim": g[0]["edition"],
                "claims": [{k_: c[k_] for k_ in ("claim_id", "edition", "arxiv_base", "title", "noun",
                                                 "evidence")} | {"label": c["review"]["label"]} for c in g],
                "developers": sorted({a for c in g for a in c["authors"]})})
    # homonym ids exist only after every group got one; fill the cross-links
    by_fold = collections.defaultdict(list)
    for e in entities:
        by_fold[e["fold"]].append(e["id"])
    for e in entities:
        e["homonym_of"] = sorted(i for i in by_fold[e["fold"]] if i != e["id"]) or None
        if e["homonym_of_registered"] and e["homonym_of_registered"] not in (e["homonym_of"] or []):
            e["homonym_of"] = sorted((e["homonym_of"] or []) + [e["homonym_of_registered"]])
    status = collections.Counter((c["review"] or {}).get("status", "candidate") for c in claims)
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


def _cites_paper(ref_texts: list[str], title: str) -> bool:
    t = dataset_key(title)[:40]
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--edition", action="append", default=[], help="e.g. iclr-2025 (repeatable)")
    ap.add_argument("--all", action="store_true", help="every edition with parsed HTML")
    ap.add_argument("--candidates", action="store_true",
                    help="only aggregate unlisted names from existing outputs into a review list")
    ap.add_argument("--introduced", action="store_true",
                    help="update config/benchmarks_introduced.json from existing outputs")
    ap.add_argument("--summary", action="store_true", help="per-edition funnel and stated-role counts")
    ap.add_argument("--review-export", metavar="TSV", help="write unreviewed introduction claims to read")
    ap.add_argument("--sample", type=int, default=0, help="with --review-export: a stratified sample of N")
    ap.add_argument("--review-import", metavar="TSV", help="record 'claim_id<TAB>label[<TAB>note]' decisions")
    ap.add_argument("--adoption", action="store_true",
                    help="count stated uses of introduced names, self vs other authors")
    args = ap.parse_args()
    if args.candidates:
        candidates()
        return 0
    if args.introduced:
        introduced()
        return 0
    if args.adoption:
        adoption()
        return 0
    if args.summary:
        summary()
        return 0
    if args.review_export:
        review_export(args.review_export, args.sample)
        return 0
    if args.review_import:
        review_import(args.review_import)
        return 0
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
    candidates()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
