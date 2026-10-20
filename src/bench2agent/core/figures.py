"""Figures in an answer, recomputed before the reader sees them.

A quote is checked by matching it against the paper. A NUMBER cannot be
matched that way — it was not written by anyone, it was computed. So it is
checked the only honest way available: the tool that produced it is run
again on this machine and every number in the claim must appear in the
result.

This is deliberately mechanical. It decides nothing about whether the
sentence around the number is fair; it decides whether the number survives
recomputation. Three outcomes, and the third is not a failure:

  ok  every number in the claim appears in the recomputed result
  no  at least one does not — it was mistyped or invented
  na  the claim could not be recomputed (a tool outside the list below, or
      an argument that no longer resolves), so nothing is claimed about it

Only deterministic tools are on the list. `search_papers` is NOT: its ranking
and its estimated total come from the search engine, and marking a figure
verified when the check is not reproducible would be worse than not marking
it at all.
"""
from __future__ import annotations

import re
import json

# tool -> the kind of argument it takes
RECOMPUTABLE = {"field_trend": "topic", "gap_scan": "topic",
                "topic_papers": "topic", "field_cards": "topic",
                "get_citations": "gid", "get_paper": "gid",
                # links and counts read from resources.json / the registry —
                # no engine, no ranking, the same answer every time
                "paper_resources": "gid", "benchmark_info": "name"}
RECOMPUTABLE.update({name: "json" for name in (
    "benchmark_scope", "benchmark_usage", "benchmark_trend", "new_benchmarks",
    "benchmark_adoption", "benchmark_evidence")})


# String fields whose digits are figures the tool stated: the trend rule's
# threshold ("abs(z) >= 2.576") and edition names ("iclr-2026"). URLs, ids
# and dates elsewhere stay out of the pool.
_TEXT_FIGURE_KEYS = {"rule", "edition", "from", "to", "first_claim"}


def benchmark_pool(obj, key=None) -> list[float]:
    """Only computed statistics, excluding IDs, quotes, dates and name digits."""
    out = []
    if isinstance(obj, dict):
        for k, value in obj.items():
            if k not in {"evidence", "claims", "surfaces", "paper_id", "paper_ids", "id",
                         "snapshot_id", "generated_at", "schema_version", "authors", "developers"}:
                out.extend(benchmark_pool(value, k))
    elif isinstance(obj, list):
        for value in obj:
            out.extend(benchmark_pool(value, key))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append(float(obj))
    elif isinstance(obj, str) and key in _TEXT_FIGURE_KEYS and len(obj) <= 400:
        # unsigned: the hyphen in "iclr-2026" is a separator, not a minus
        out.extend(_val(m.group(0)) for m in _UNSIGNED_NUM.finditer(obj))
    return out

# Ids are not figures. A gid is a five-digit number that would make almost any
# claimed count match something, so paper lists never enter the pool.
SKIP_KEYS = {"gid", "gids", "attackers", "namers", "results", "cards",
             "citing", "cited_by", "cites", "neighbors", "id"}

# 3,324 is one number. The comma-group form must be tried first, or the scan
# reports "324" as an unexplained figure — measured on a real answer.
_NUM = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")
_YEAR = re.compile(r"^(?:19|20)\d\d$")
_UNSIGNED_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def _val(raw: str) -> float:
    return float(raw.replace(",", ""))


def pool(obj, out: list | None = None) -> list[float]:
    """Every figure in a tool result, ids excluded."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            # a numeric KEY is a figure too: by_year is {"2025": 13}, and a
            # claim that names the edition is quoting the result, not inventing
            # (in-process the key is an int; through JSON the agent saw "2025")
            if isinstance(k, int) and not isinstance(k, bool):
                out.append(float(k))
            elif isinstance(k, str) and _NUM.fullmatch(k):
                out.append(float(k))
            if k in SKIP_KEYS:
                # the LIST is not a figure, but HOW MANY is — "28 papers cite
                # this one" is the count of a list this pool otherwise skips
                if isinstance(v, list):
                    out.append(float(len(v)))
                continue
            pool(v, out)
    elif isinstance(obj, list):
        for v in obj:
            pool(v, out)
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        for m in _NUM.findall(obj):
            out.append(_val(m))          # tool notes print "19,071" too
    return out


# the agent writes "z −0.09" with a typographic minus; same length as "-",
# so offsets computed on the normalised text still index the original
_MINUS = str.maketrans({"\u2212": "-"})


def claimed(claim: str) -> list[tuple[str, float]]:
    claim = claim.translate(_MINUS)
    return [(m.group(0), _val(m.group(0))) for m in _NUM.finditer(claim)
            if not _identifier_number(claim, m)]


_ARXIV_ID = re.compile(r"\d{4}\.\d{4,5}(?:v\d+)?")


def _identifier_number(text: str, match) -> bool:
    """Digits in CIFAR-10, MATH500 and 3D-POPE are names, not statistics;
    so are arXiv ids (2306.03310) and the year in an edition name
    ("ICLR 2026", "NeurIPS 2025"), which tool results carry as strings."""
    before, after = text[:match.start()], text[match.end():]
    raw = match.group(0)
    if _val(raw) == 1000 and (re.search(r"\bper\s*$", before, re.I)
                              or re.match(r"\s*(?:편|개|논문)\s*당", after)):
        return True  # the fixed rate unit in "500 per 1,000 papers"
    if _ARXIV_ID.fullmatch(raw) and not re.search(r"\d$", before) and not re.match(r"\d", after):
        return True
    if _YEAR.fullmatch(raw) and (re.search(r"[A-Za-z]{2,}\s+$", before) or re.search(r"[→>\-–]\s*$", before)
                                 or re.match(r"\s*(?:→|->|–|edition|년|회차)", after)):
        return True  # "ICLR 2026", "2024→2025": edition years, not counts
    return bool(re.search(r"[A-Za-z][-_/]?$", before) or re.match(r"[A-Za-z]", after))


def _matches(raw: str, val: float, vals: list[float]) -> bool:
    """A figure matches if some computed value rounds to it at ITS precision.

    The claim's own precision sets the tolerance: "4.4" accepts 4.43, "1.15"
    does not accept 1.2. Rounding is where honest transcription differs from
    invention, and this is the line between them.
    """
    dp = len(raw.split(".")[1]) if "." in raw else 0
    return any(round(x, dp) == round(val, dp) for x in vals)


def recompute(tool: str, arg: str, cache: dict, run=None) -> list[float] | None:
    """The tool's figures, or None when the claim cannot be recomputed."""
    kind = RECOMPUTABLE.get(tool)
    if kind is None:
        return None
    key = (tool, arg)
    if key in cache:
        return cache[key]
    if run is None:                       # imported here: mcp loads the corpus
        from . import mcp
        run = {t["name"]: t["fn"] for t in [*mcp.TOOLS, *mcp._benchmark_tools()]}
    fn = run.get(tool)
    if fn is None:
        cache[key] = None
        return None
    try:
        # the kind is the tool's argument name; only gid is numeric
        args = json.loads(arg) if kind == "json" else {kind: int(arg) if kind == "gid" else arg}
        if not isinstance(args, dict):
            raise ValueError("Expected tool arguments object")
        out = fn(args)
        vals = None if (isinstance(out, dict) and out.get("error")) else (
            benchmark_pool(out) if kind == "json" else pool(out))
    except Exception:                     # noqa: BLE001 — an unresolvable arg
        vals = None
    cache[key] = vals
    return vals


def canonical_args(arg) -> str:
    """One spelling for a tool's arguments: parsed JSON re-serialised with
    sorted keys, so an anchor and a log entry compare regardless of spacing
    or key order. Unparseable text compares as itself."""
    if isinstance(arg, (dict, list)):
        return json.dumps(arg, sort_keys=True, ensure_ascii=False)
    try:
        return json.dumps(json.loads(arg), sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(arg).strip()


class TurnLog:
    """The tool calls one turn actually made, as the MCP server recorded
    them (name, arguments, result, revision). Figures are checked against
    these results: a number the model printed must come from a result the
    model was shown, not from a call recomputed on its behalf."""

    def __init__(self, entries: list[dict]):
        self.entries = entries
        self._by = {}
        for e in entries:
            self._by.setdefault((e.get("name"), canonical_args(e.get("arguments") or {})), []).append(e)

    def results(self, tool: str, arg) -> list[dict] | None:
        return self._by.get((tool, canonical_args(arg)))

    def pool(self, tool: str, arg) -> list[float] | None:
        found = self.results(tool, arg)
        if found is None:
            return None
        out: list[float] = []
        for e in found:
            out.extend(benchmark_pool(e.get("result")))
        return out

    def all_values(self) -> list[float]:
        out: list[float] = []
        for e in self.entries:
            out.extend(benchmark_pool(e.get("result")))
        return out


def check(tool: str, arg: str, claim: str, cache: dict | None = None, log: TurnLog | None = None) -> dict:
    cache = {} if cache is None else cache
    nums = claimed(claim)
    if log is not None:
        vals = log.pool(tool, arg)
        if vals is None:
            return {"state": "no", "tool": tool, "arg": arg, "n": len(nums), "missing": [raw for raw, _ in nums],
                    "why": "this turn did not call the tool with these arguments"}
    else:
        vals = recompute(tool, arg, cache)
    if vals is None:
        return {"state": "na", "tool": tool, "arg": arg, "n": len(nums),
                "why": "not recomputable"}
    if not nums:
        return {"state": "na", "tool": tool, "arg": arg, "n": 0,
                "why": "no figure in the claim"}
    missing = [raw for raw, v in nums if not _matches(raw, v, vals)]
    return {"state": "no" if missing else "ok", "tool": tool, "arg": arg,
            "n": len(nums), "missing": missing}


# ---------------------------------------------------------------- the auto pass
# Requiring the agent to anchor every figure does not work: measured on three
# real answers, only 38% of the numbers it printed sat inside an anchor, and it
# re-printed bare copies of figures it had just anchored. Verification cannot
# depend on the writer's cooperation — so the server checks EVERY number against
# the tools this turn actually called, anchor or no anchor.
#
# Of the 45 bare numbers in that sample: 84% appeared verbatim in a tool result,
# 7% were a simple ratio or difference of two of them, and the remaining 9% were
# a thousands-separator parsing bug (since fixed), the project's own significance
# threshold, and a gid the agent printed. None was invented.

def derived_set(vals: list[float]) -> set:
    """Figures a reader can get from two computed ones — a share, a difference.

    An agent that writes "31 of 705" and then "4.4%" did arithmetic, not
    invention, and marking that unexplained would be wrong. The combinations
    are fixed and mechanical; nothing here judges whether the arithmetic was
    the RIGHT thing to compute.
    """
    out: set = set()
    uniq = sorted(set(vals))[:120]                   # bounded: pairs are O(n^2)
    for i, a in enumerate(uniq):
        for b in uniq[i + 1:]:
            for c in (a - b, b - a, a + b,
                      (a / b * 100) if b else None, (b / a * 100) if a else None,
                      (a / b) if b else None, (b / a) if a else None):
                if c is None or c < 0 or c > 1e7:
                    continue
                out.add(round(c, 0))
                out.add(round(c, 1))
                out.add(round(c, 2))
    return out


def turn_pool(trail: list, cache: dict | None = None, log: TurnLog | None = None) -> tuple[list[float], list[str]]:
    """Everything the tools of THIS turn returned: from the turn's own log
    when there is one, recomputed from the trail otherwise."""
    cache = {} if cache is None else cache
    if log is not None:
        return log.all_values(), [f"{e.get('name')}:{canonical_args(e.get('arguments') or {})}" for e in log.entries]
    vals: list[float] = []
    used: list[str] = []
    for step in trail or []:
        name = step.get("name") if isinstance(step, dict) else None
        arg = (step.get("arg") if isinstance(step, dict) else "") or ""
        if name in RECOMPUTABLE and arg:
            got = recompute(name, arg, cache)
            if got:
                vals += got
                used.append(f"{name}:{arg}")
    return vals, used


def scan(text: str, vals: list[float], deriv: set, skip: set) -> list[tuple]:
    text = text.translate(_MINUS)
    """(start, end, raw, verdict) for every figure printed in this text.

    Verdicts: 'ok' (in a tool result), 'derived' (arithmetic of two of them),
    'no' (in neither). Years and the answer's own gids are not figures.
    """
    out = []
    for m in _NUM.finditer(text):
        if _identifier_number(text, m):
            continue
        raw = m.group(0)
        if _YEAR.fullmatch(raw):
            continue
        v = _val(raw)
        if v in skip:
            continue
        dp = len(raw.split(".")[1]) if "." in raw else 0
        if any(round(x, dp) == round(v, dp) for x in vals):
            verdict = "ok"
        elif round(v, dp) in deriv:
            verdict = "derived"
        else:
            verdict = "no"
        out.append((m.start(), m.end(), raw, verdict))
    return out


# ---------------------------------------------------------------- connected figures
# The auto pass above checks a printed number against EVERY value the turn's
# tools returned, so any 63 anywhere could stand behind "63 editions" (seen on
# a real answer, 2026-10-16). For the six benchmark tools a number is now
# verified only when it is CONNECTED: a value in the turn's tool log whose
# context — tool call, revision, benchmark, edition, role, author group,
# metric — agrees with every cue the number's own clause gives. A number whose
# clause gives no cue, or whose cues no fact satisfies, stays unverified; a
# number whose cues ARE satisfied by facts with other values is contradicted.
# Not a natural-language verifier: a fixed list of cues, read near the number.

_EDITION = re.compile(r"\b([a-z]+)-(\d{4})\b")
_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+(?:Z|[+-]\d{2}:\d{2})?)?(?!\d)|(?<!\d)\d{4}년\s*\d{1,2}월(?:\s*\d{1,2}일)?")
_ROLE_KEYS = {"evaluates_on", "trains_on"}
_GROUP_KEYS = {"others", "self", "undetermined"}
_VENUES = ("aaai", "acl", "corl", "cvpr", "eccv", "emnlp", "iccv", "iclr", "icml", "naacl", "neurips")

# metric families — never pooled: a number is connected inside ONE family
USAGE = {"papers", "n_papers", "using_papers"}                                       # paper-edition observations
ADOPTION = {"others", "self", "undetermined", "sum:groups", "unattributed_using_papers", "before_claim"}   # unique papers
UNATTRIBUTED_OBSERVATIONS = {"papers_with_unattributed_benchmark_mentions", "unattributed_papers"}
DENOM = {"parsed_papers", "total_papers", "arxiv_matched", "in_scope_parsed", "field_labelled_papers", "unlabelled_papers"}
TOTALS = DENOM | UNATTRIBUTED_OBSERVATIONS | {"total", "catalogue_entries", "count:venues"}
EDITIONS = {"count:editions", "count:by_edition", "editions", "n_editions"}
RATE = {"per_1000", "share_per_1000", "rate_per_1000"}
_COUNTS = USAGE | ADOPTION                                                           # what a bare "papers" unit can mean
_ANY = _COUNTS | {"count:*", "catalogue_entries", "total", "total_benchmarks"}


def family(metric: str) -> str:
    return ("usage" if metric in USAGE else "adoption" if metric in ADOPTION else "denominator" if metric in DENOM
            else "unattributed_observations" if metric in UNATTRIBUTED_OBSERVATIONS
            else "editions" if metric in EDITIONS else "rate" if metric in RATE else metric)


# the unit right after a number sets its family
_UNIT_AFTER = [(r"\s*(?:개\s*)?(?:(?:회차|에디션)(?!\s*별)|editions?(?![A-Za-z]))", EDITIONS),   # "에디션별로는" (by edition) is no unit
               (r"\s*(?:편\s*(?:이|가|이\s*)?\s*파싱|parsed\s+papers?(?![A-Za-z])|papers?\s+(?:were\s+)?parsed)", {"parsed_papers", "in_scope_parsed"}),   # "4,252편 파싱되었습니다"
               (r"\s*(?:편|papers?(?![A-Za-z])|논문)", _COUNTS),
               (r"\s*(?:‰|/\s*1,?000(?!\d)|per\s*1,?000(?!\d)|(?:편|개)?\s*당\s*1,?000(?!\d)|/\s*천)", RATE), (r"\s*%", {"percent"}),
               (r"\s*(?:건|개|items?(?![A-Za-z]))", _ANY)]
# words BEFORE the number: a specific word beats a generic one; among specific ones the nearest wins
_SPECIFIC_WORDS = [
    (r"(?<!논문-)(?<!paper-)(?:회차|에디션|editions?)", EDITIONS), (r"논문-에디션(?:\s*관측)?|paper-edition(?:\s*observations?)?", {"parsed_papers", "in_scope_parsed"}),
    (r"‰|per\s*1,?000(?:\s+(?:parsed\s+)?papers?)?|1,?000\s*(?:편|개)?\s*당|천\s*편당", RATE),   # "per 1,000 parsed papers" is one rate phrase (r"(?<![A-Za-z])z(?![A-Za-z])|z-?값", {"z"}),
    (r"분모|파싱|parsed", {"parsed_papers", "in_scope_parsed"}), (r"매칭|matched", {"arxiv_matched"}),
    (r"전체|(?<![A-Za-z])total(?![A-Za-z])|총", {"total_papers", "parsed_papers", "in_scope_parsed", "total", "catalogue_entries"}),
    (r"미라벨|라벨\s*없|unlabell?ed", {"unlabelled_papers"}), (r"(?<!미)라벨(?!\s*없)|(?<!un)labell?ed", {"field_labelled_papers"}),
    (r"미귀속|미해소|unattributed|unresolved", {"unattributed_using_papers"} | UNATTRIBUTED_OBSERVATIONS),
    (r"papers_with_unattributed_benchmark_mentions|unattributed_papers", UNATTRIBUTED_OBSERVATIONS),
    (r"(?<!미)귀속|(?<!un)attributed|채택|adoption|고유|unique|중복\s*제거", ADOPTION - {"unattributed_using_papers", "before_claim"}),
    (r"이전|before[_ ]claim", {"before_claim"}), (r"학회|venues?", {"count:venues"})]
_GENERIC_WORDS = [(r"사용|(?<![A-Za-z])uses?(?![A-Za-z])|usage|관측|observations?", USAGE)]
_CONTEXT_WORDS = [(r"타\s*저자|다른\s*저자|(?<![A-Za-z])others?(?![A-Za-z])", "group", "others"), (r"자기|자체\s*저자|(?<![A-Za-z])self(?![A-Za-z])", "group", "self"),
                  (r"미확정|(?<![A-Za-z])undetermined(?![A-Za-z])", "group", "undetermined"), (r"평가|evaluat", "role", "evaluates_on"), (r"학습|훈련|train", "role", "trains_on")]


def fold(name: str) -> str:
    """Case, spaces, hyphens and underscores only: '+' and other identity characters stay ('HumanEval' != 'HumanEval+')."""
    return re.sub(r"[\s_-]", "", name.lower())


def facts(log: TurnLog) -> list[dict]:
    """(value, metric, context) for every number in the turn's benchmark tool results. A value inside one
    benchmark's result carries its id, except the scope-wide unresolved-observation diagnostic.
    That diagnostic also spans all roles, regardless of the query's selected role."""
    out: list[dict] = []

    def walk(o, ctx, key):
        if isinstance(o, dict):
            c = dict(ctx)
            if isinstance(o.get("id"), str) and isinstance(o.get("name"), str):
                c["benchmark_id"] = o["id"]
                c["surfaces"] = {o["id"], o["name"], *[a for a in o.get("aliases") or [] if isinstance(a, str)]}
            if isinstance(o.get("edition"), str):
                c["edition"] = o["edition"].lower()
            if isinstance(o.get("role"), str) and o["role"] in _ROLE_KEYS:
                c["role"] = o["role"]
            if key in {"editions", "by_edition", "by_year"} or (o and all(_EDITION.fullmatch(str(k)) for k in o)):
                out.append({"value": float(len(o)), "metric": f"count:{key}" if key else "count:editions", "ctx": c})
            for k, v in o.items():
                if k in {"evidence", "claims", "surfaces", "paper_id", "paper_ids", "snapshot_id", "generated_at", "authors", "developers",
                         "note", "url", "title", "introducing_paper", "locations"}:
                    continue
                cc = dict(c)
                if isinstance(k, str) and _EDITION.fullmatch(k):
                    cc["edition"] = k.lower()
                if k in _ROLE_KEYS:
                    cc["role"] = k
                if k in _GROUP_KEYS:
                    cc["group"] = k
                walk(v, cc, k)
        elif isinstance(o, list):
            if key:
                out.append({"value": float(len(o)), "metric": f"count:{key}", "ctx": ctx})
            for v in o:
                walk(v, ctx, key)
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            if key in UNATTRIBUTED_OBSERVATIONS:
                # Coverage spans all benchmarks and roles, even when nested
                # in a single-benchmark or evaluation/training result.
                ctx = {k: v for k, v in ctx.items() if k not in {"benchmark_id", "surfaces", "role"}}
            out.append({"value": float(o), "metric": key or "", "ctx": ctx})

    for e in log.entries:
        if RECOMPUTABLE.get(e.get("name")) != "json":
            continue
        base = {"tool": e.get("name"), "args": canonical_args(e.get("arguments") or {}), "revision": e.get("revision")}
        args = e.get("arguments") or {}
        if isinstance(args, dict) and isinstance(args.get("role"), str):
            base["role"] = args["role"]
        walk(e.get("result"), base, None)
    # the attributed total of one role (others + self + undetermined), within one object
    groups: dict = {}
    for f in out:
        if f["metric"] in _GROUP_KEYS:
            k = json.dumps({x: (sorted(v) if isinstance(v, set) else v) for x, v in f["ctx"].items() if x != "group"}, sort_keys=True)
            groups.setdefault(k, []).append(f)
    for fs in groups.values():
        if len(fs) >= 2:
            out.append({"value": sum(f["value"] for f in fs), "metric": "sum:groups", "ctx": {k: v for k, v in fs[0]["ctx"].items() if k != "group"}})
    return out


_B_BEFORE = r"(?<![a-z0-9+])(?<![a-z0-9][-_])"
_B_AFTER = r"(?![a-z0-9+])(?![-_][a-z0-9])"


def _surfaces(fs: list[dict]) -> tuple[dict, dict, dict]:
    """ids (matched exactly), and folded names -> the ids they can mean (a homonym maps to several)."""
    ids, names, originals = {}, {}, {}
    for f in fs:
        bid = f["ctx"].get("benchmark_id")
        if not bid:
            continue
        ids[bid.lower()] = bid
        for s in (f["ctx"].get("surfaces") or set()) - {bid}:
            if len(fold(s)) >= 3:
                names.setdefault(fold(s), set()).add(bid)
                originals.setdefault(fold(s), s)
    return ids, names, originals


def _mentions(text: str, surf: tuple, _unused=None) -> set[str] | None:
    """Benchmark ids named in this text, None when nothing is named. An id is read exactly and
    claims its span first; a name (case, spaces and hyphens folded, '+' kept) yields every id it can mean."""
    ids_map, names, originals = surf
    low, found = text.lower(), set()
    shown = set(originals.values())
    for lid in sorted(ids_map, key=len, reverse=True):
        bid = ids_map[lid]
        if bid in shown:
            continue                                   # an id spelled exactly like a display name reads as that name
        for m in re.finditer(r"(?<![A-Za-z0-9+])(?<![A-Za-z0-9][-_])" + re.escape(bid) + r"(?![A-Za-z0-9+])(?![-_][A-Za-z0-9])", text):
            found.add(bid)                             # the id, in its own spelling and case
            low = low[:m.start()] + " " * (m.end() - m.start()) + low[m.end():]
    exact = set(found)
    for folded, original in originals.items():
        pat = re.sub(r"\\[ \-_]|_", "[-_\\\\s]?", re.escape(original.lower()))
        if re.search(_B_BEFORE + pat + _B_AFTER, low):
            # a name next to one of its own ids means that id: "Noisy-MNIST (id: `noisy-mnist`)"
            found |= (names[folded] & exact) or names[folded]
    return found or None


def _clause(text: str, a: int, b: int) -> tuple[str, str]:
    start = max(text.rfind(ch, 0, a) for ch in ("\n", ". ", "。", "|", "; ", "·", "(", "—", ", ", " + ", "→"))
    start = 0 if start < 0 else start + 1
    pair = re.match(r"\s*(?:/|of(?![A-Za-z]))\s*\d[\d,]*(?:\.\d+)?", text[b:])
    if pair:                                                # "10 of 4252 parsed papers in iclr-2026": the count shares
        b2 = b + pair.end()                                 # what follows its denominator
        ends = [i for i in (text.find(ch, b2) for ch in ("\n", ". ", "|", ";", ", ", "(", ")", " + ", "—", " – ")) if i >= 0]
        return text[start:a], text[b:min(min(ends) if ends else len(text), b2 + 30)]
    ends = [i for i in (text.find(ch, b) for ch in ("\n", ". ", "|", ";", ", ", "(", ")", " + ", "—", " – ")) if i >= 0]
    return text[start:a], text[b:min(min(ends) if ends else len(text), b + 30)]


def _section(text: str, a: int, surf, raw=None) -> set[str] | None:
    """The benchmark of the number's section: the nearest heading (or a line led by a bold name) above it.
    The walk stops there: a heading that names no benchmark ends the previous section."""
    for line in reversed(text[:a].split("\n")[:-1]):
        if re.match(r"\s*#{1,6}\s", line):
            return _mentions(line, surf)
        if re.match(r"\s*[-*]?\s*\*\*[^*]+\*\*", line):
            ids = _mentions(re.match(r"\s*[-*]?\s*\*\*([^*]+)\*\*", line).group(1), surf)
            if ids:
                return ids
    return None


def _metric_cues(before: str, after: str) -> set | None:
    unit = next((set(fam) for rx, fam in _UNIT_AFTER if re.match(rx, after, re.I)), None)
    if unit is None and re.match(r"\s*(?:/|of(?![A-Za-z]))\s*\d", after) and not re.search(r"(?:분모|파싱|parsed|매칭|matched)\s*$", before, re.I):
        return set(USAGE)                                      # "0/547", "82 of 513": an edition's use count ...
        # ... unless a word right before says what it is ("parsed 1894 of 2296 accepted")
    if unit is None and re.search(r"\d\s*(?:/|(?<![A-Za-z])of)\s*$", before):
        return set(DENOM)                                      # ... over its denominator
    best = None
    for rx, fam in _SPECIFIC_WORDS:
        for m in re.finditer(rx, before, re.I):
            if best is None or m.end() > best[0]:
                best = (m.end(), fam)
    word = set(best[1]) if best else None
    if word is None:
        for rx, fam in _GENERIC_WORDS:
            if re.search(rx, before, re.I):
                return {"__generic__"} | (set(fam) & unit if unit else set(fam))   # a guess: may connect, never contradicts
    if unit is None:
        return word
    if word is None:
        return unit
    if word & UNATTRIBUTED_OBSERVATIONS and unit in (_COUNTS, _ANY):
        return word                                             # paper-edition diagnostic, not adoption by the "papers" unit
    narrowed = {x for x in word if x in unit or ("count:*" in unit and x.startswith("count:"))}
    if narrowed:
        return narrowed
    # a denominator or total word with a count unit ("분모 3,059편", "전체 4,417편", "미라벨 35,504건"): the word says what is counted
    return set(word) if set(word) <= TOTALS and (unit == _COUNTS or unit == _ANY) else unit


def _context_cues(before: str, after: str) -> dict:
    right_after = re.split(r"\d", after, maxsplit=1)[0][:12]
    c: dict = {}
    for rx, kind, val in _CONTEXT_WORDS:
        if re.search(rx, before + " " + right_after, re.I):
            c[kind] = val
    rx = r"(?<![a-z])(" + "|".join(_VENUES) + r")[\s-]?((?:19|20)\d\d)(?!\d)"
    eds = {f"{v}-{y}" for v, y in re.findall(rx, before.lower())}
    # after the number, an edition followed by another number is that number's ("…before_claim 0
    # 에디션별로는 neurips-2025에 타 저자 3편": neurips-2025 is the 3's); a trailing one is this one's
    low = after.lower()
    for m in re.finditer(rx, low):
        if not re.search(r"\d", re.sub(rx, " ", low[m.end():])):
            eds.add(f"{m.group(1)}-{m.group(2)}")
    if eds:
        c["edition"] = eds
    return c


def _tables(text: str) -> list[dict]:
    """Markdown tables whose header names an identity column (benchmark or edition): per data
    row its span, the identity cell, and each cell's span with its column header."""
    out, lines, pos = [], text.split("\n"), 0
    offs = []
    for ln in lines:
        offs.append(pos); pos += len(ln) + 1
    i = 0
    while i < len(lines) - 1:
        if lines[i].strip().startswith("|") and re.fullmatch(r"\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*", lines[i + 1]):
            header = [h.strip() for h in lines[i].strip().strip("|").split("|")]
            ident = next((j for j, h in enumerate(header) if re.search(r"benchmark|벤치마크|dataset|데이터셋|(?<![A-Za-z])name(?![A-Za-z])|이름", h, re.I)), None)
            ed_col = next((j for j, h in enumerate(header) if re.search(r"edition|회차|에디션|venue|학회", h, re.I)), None)
            k = i + 2
            while k < len(lines) and lines[k].strip().startswith("|"):
                cells, start = [], offs[k] + lines[k].index("|") + 1
                for part in lines[k].strip().strip("|").split("|"):
                    cells.append((start, start + len(part), part)); start += len(part) + 1
                out.append({"span": (offs[k], offs[k] + len(lines[k])), "header": header, "cells": cells,
                            "ident": cells[ident][2].strip() if ident is not None and ident < len(cells) else None,
                            "edition": cells[ed_col][2].strip() if ed_col is not None and ed_col < len(cells) else None,
                            "supported": ident is not None or ed_col is not None, "ident_col": ident, "ed_col": ed_col})
                k += 1
            i = k
        else:
            i += 1
    return out


def _satisfies(f: dict, c: dict) -> bool:
    ctx, metric = f["ctx"], f["metric"]
    if "metric" in c and metric not in c["metric"] and not ("count:*" in c["metric"] and metric.startswith("count:")):
        return False
    ids = c.get("benchmark_ids")
    if ids is not None and ctx.get("benchmark_id") not in ids:
        return False                                   # a named benchmark: its own values only, never a scope-wide one
    if "edition" in c and ctx.get("edition") not in c["edition"]:
        return False
    if "role" in c and ctx.get("role") != c["role"]:
        return False
    if "group" in c and ctx.get("group") != c["group"]:
        return False
    return True


def _judge(raw: str, v: float, c: dict, fs: list[dict]) -> tuple[str, str]:
    dp = len(raw.split(".")[1]) if "." in raw else 0
    guessed = "metric" in c and "__generic__" in c["metric"]
    if guessed:
        c = {**c, "metric": c["metric"] - {"__generic__"}}
    if c.get("ambiguous_name"):
        return "na", "the name means several benchmark ids"
    if not (any(k in c for k in ("role", "group")) or ("metric" in c and c["metric"] != _ANY)):
        return "na", "no cue says what it counts"
    cand = [f for f in fs if _satisfies(f, c)]
    fams = {family(f["metric"]) for f in cand}
    if {"usage", "adoption"} <= fams and "metric" in c and c["metric"] >= _COUNTS:
        return "na", "usage (paper-edition observations) or adoption (unique papers) is not stated"
    if {"unattributed_observations", "adoption"} <= fams:
        return "na", "scope-wide unresolved observations or a benchmark's unique unattributed papers is not stated"
    hits = [f for f in cand if round(f["value"], dp) == round(v, dp)]
    benchmark_stat = any(family(f["metric"]) not in ("denominator", "editions") for f in hits)
    if c.get("benchmark_ids") is None and benchmark_stat and len({f["ctx"].get("benchmark_id") for f in hits if f["ctx"].get("benchmark_id")}) > 1 \
            and all(f["ctx"].get("benchmark_id") for f in hits):
        return "na", "the value belongs to several benchmarks and the text names none"
    if hits:
        return "ok", "connected"
    if cand and guessed:
        return "na", "only a generic word says what it counts"
    if cand:
        vals = sorted({round(f["value"], dp) for f in cand})[:4]
        return "no", f"the tool results for this context say {', '.join(f'{x:g}' for x in vals)}"
    return "na", "no tool result has this context"


def scan_connected(text: str, fs: list[dict], date_strings: set[str], skip: set) -> list[tuple]:
    """(start, end, raw, verdict, why): 'ok' connected, 'no' contradicted
    (facts with these cues hold another value), 'na' not connected, 'date-ok' /
    'date-no' for a date found / not found verbatim in this turn's results.
    Dates are not statistics: they are never counted as verified figures."""
    text = text.translate(_MINUS)
    surf, raw_names = _surfaces(fs), None
    tables = _tables(text)
    out, taken = [], []
    for m in _DATE.finditer(text):
        raw = m.group(0)
        out.append((m.start(), m.end(), raw, "date-ok" if raw[:10] in date_strings else "date-no", "date"))
        taken.append((m.start(), m.end()))
    for m in _NUM.finditer(text):
        if any(a <= m.start() < b for a, b in taken) or _identifier_number(text, m):
            continue
        raw, v = m.group(0), _val(m.group(0))
        line_start = text.rfind("\n", 0, m.start()) + 1
        if re.fullmatch(r"\s*(?:#{1,6}\s*|[-*]\s*)?", text[line_start:m.start()]) and re.match(r"[.)]\s", text[m.end():m.end() + 2]):
            continue                                            # list numbering
        if text[max(0, m.start() - 1):m.start()] == "[" and text[m.end():m.end() + 1] == "]":
            continue                                            # [1] reference marker
        if v == 1000 and re.search(r"/\s*$", text[:m.start()]):
            continue                                            # the fixed rate unit in "0.0/1000"
        if text[m.end():m.end() + 1] == "차" and text[max(0, m.start() - 1):m.start()] != "회":
            continue                                            # "1차": an ordinal
        if v in skip:
            continue
        if re.match(r"\s*(?:이|가)\s*(?:아니|아닙|아님)", text[m.end():m.end() + 6]) or re.search(r"\bnot\s+$", text[max(0, m.start() - 6):m.start()]):
            continue                                            # a negation, not a reported value
        if _YEAR.fullmatch(raw) and (re.search(r"(?:19|20)\d\d\s*[/~–]\s*$", text[max(0, m.start() - 8):m.start()])
                                     or re.match(r"\s*[/~–]\s*(?:19|20)\d\d", text[m.end():m.end() + 8])):
            continue                                            # edition-year shorthand
        row = next((t for t in tables if t["span"][0] <= m.start() < t["span"][1]), None)
        if row is not None:
            cell = next(((j, cs) for j, cs in enumerate(row["cells"]) if cs[0] <= m.start() < cs[1]), None)
            if cell is None or not row["supported"] or cell[0] in (row["ident_col"], row["ed_col"]):
                if cell is not None and cell[0] in (row["ident_col"], row["ed_col"]):
                    continue                                    # a number inside the identity cell is part of a name
                out.append((m.start(), m.end(), raw, "na", "a table this check cannot read (no benchmark or edition column)"))
                continue
            head = row["header"][cell[0]] if cell[0] < len(row["header"]) else ""
            before, after = head + " ", text[m.end():cell[1][1]]
            c = {"metric": _metric_cues(before, after)} if _metric_cues(before, after) else {}
            c.update(_context_cues(head, after))
            if "group" in c:
                c["metric"] = (c.get("metric", ADOPTION) & ADOPTION) or set(ADOPTION)
            if row["edition"]:
                c.update({k: x for k, x in _context_cues(row["edition"], "").items() if k == "edition"})
            if row["ident"]:
                ids = _mentions(row["ident"], surf, raw_names)
                if ids is None:
                    out.append((m.start(), m.end(), raw, "na", "the row's benchmark is not in this turn's results")); continue
                c["benchmark_ids"] = ids
                c["ambiguous_name"] = len(ids) > 1
        else:
            before, after = _clause(text, m.start(), m.end())
            if _YEAR.fullmatch(raw) and _metric_cues(before, after) is None:
                continue                                        # a year as context, not a count
            c = {}
            mc = _metric_cues(before, after)
            if mc:
                c["metric"] = mc
            c.update(_context_cues(before, after))
            if "group" in c:                                    # an author group only exists for adoption counts
                c["metric"] = (c.get("metric", ADOPTION) & ADOPTION) or set(ADOPTION)
            ids = _mentions(before + " " + after, surf, raw_names)
            if ids is None and not (c.get("metric") and c["metric"] <= EDITIONS):
                para_start = max(text.rfind("\n\n", 0, m.start()), text.rfind("\n#", 0, m.start()))
                ids = _mentions(text[para_start + 1 if para_start >= 0 else 0:m.start()], surf)   # the paragraph so far
                if ids is None:
                    ids = _section(text, m.start(), surf, raw_names)   # a heading does not make a count of editions a benchmark's
            if ids is not None:
                c["benchmark_ids"] = ids
                c["ambiguous_name"] = len(ids) > 1
        verdict, why = _judge(raw, v, c, fs)
        out.append((m.start(), m.end(), raw, verdict, why))
    return sorted(out)


def date_strings(log: TurnLog) -> set[str]:
    out: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            out.update(m.group(0)[:10] for m in re.finditer(r"\b\d{4}-\d{2}-\d{2}", o))
    for e in log.entries:
        walk(e.get("result"))
    return out
