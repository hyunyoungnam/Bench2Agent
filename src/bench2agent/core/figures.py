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
from decimal import Decimal, ROUND_HALF_UP

from .metrics import percentage

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
        if vals is None:                               # not called with these arguments: no evidence, not a contradiction
            return {"state": "na", "tool": tool, "arg": arg, "n": len(nums), "missing": [raw for raw, _ in nums],
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
PERCENT = {"share_percent", "percent"}
_SCOPE_WIDE = EDITIONS | UNATTRIBUTED_OBSERVATIONS | {"catalogue_entries", "count:venues", "total_benchmarks"}
_COUNTS = USAGE | ADOPTION                                                           # what a bare "papers" unit can mean
_ANY = _COUNTS | {"count:*", "catalogue_entries", "total", "total_benchmarks"}


def family(metric: str) -> str:
    return ("usage" if metric in USAGE else "adoption" if metric in ADOPTION else "denominator" if metric in DENOM
            else "unattributed_observations" if metric in UNATTRIBUTED_OBSERVATIONS
            else "editions" if metric in EDITIONS else "rate" if metric in RATE
            else "percentage" if metric in PERCENT else metric)


# the unit right after a number sets its family
_UNIT_AFTER = [(r"\s*(?:개\s*)?(?:(?:회차|에디션)(?!\s*별)|editions?(?![A-Za-z]))", EDITIONS),   # "에디션별로는" (by edition) is no unit
               (r"\s*(?:편\s*(?:이|가|이\s*)?\s*(?:파싱|분석)|(?:parsed|analy[sz]ed)\s+papers?(?![A-Za-z])|papers?\s+(?:were\s+)?(?:parsed|analy[sz]ed))", {"parsed_papers", "in_scope_parsed"}),   # "4,252편 파싱되었습니다"
               (r"\s*(?:listed\s+papers?(?![A-Za-z])|papers?\s+listed|편\s*(?:이|가)?\s*수록)", {"total_papers"}),   # "116,988 listed papers"
               (r"\s*(?:편|papers?(?![A-Za-z])|논문)", _COUNTS),
               (r"\s*(?:‰|/\s*1,?000(?!\d)|per\s*1,?000(?!\d)|(?:편|개)?\s*당\s*1,?000(?!\d)|/\s*천)", RATE), (r"\s*(?:%|퍼센트)", PERCENT),
               (r"\s*(?:개\s*)?files?(?![A-Za-z])", {"n_files"}),          # a paper link's file list: "96 files"
               (r"\s*(?:건|개|items?(?![A-Za-z]))", _ANY)]
# words BEFORE the number: a specific word beats a generic one; among specific ones the nearest wins
_SPECIFIC_WORDS = [
    (r"(?<!논문-)(?<!paper-)(?:회차|에디션|editions?)", EDITIONS), (r"논문-에디션(?:\s*관측)?|paper-edition(?:\s*observations?)?", {"parsed_papers", "in_scope_parsed"}),
    (r"‰|per\s*1,?000(?:\s+(?:parsed(?:\s+papers?)?|papers?))?|1,?000\s*(?:편|개)?\s*당|천\s*편당", RATE),   # "per 1,000 parsed (papers)" is one rate phrase
    (r"%|percent(?:age)?|백분율|퍼센트|비율|share(?:\s+of\s+(?:(?:analy[sz]ed|parsed)\s+)?papers?)?", PERCENT),
    (r"(?<![A-Za-z])z(?![A-Za-z])|z-?값", {"z"}),
    (r"분모|파싱|분석|parsed|analy[sz]ed", {"parsed_papers", "in_scope_parsed"}), (r"매칭|matched", {"arxiv_matched"}),
    (r"(?<![A-Za-z])listed|수록|목록", {"total_papers"}),
    (r"(?<!학회 )(?<!학회)전체(?!\s*(?:에서|의|적으로|대비|를|로))|(?<![A-Za-z])total(?![A-Za-z])|총", {"__total__", "total_papers", "parsed_papers", "in_scope_parsed", "total", "catalogue_entries"}),
    (r"미라벨|라벨\s*없|unlabell?ed", {"unlabelled_papers"}), (r"(?<!미)라벨(?!\s*없)|(?<!un)labell?ed", {"field_labelled_papers"}),
    (r"미귀속|미해소|unattributed|unresolved", {"unattributed_using_papers"} | UNATTRIBUTED_OBSERVATIONS),
    (r"papers_with_unattributed_benchmark_mentions|unattributed_papers", UNATTRIBUTED_OBSERVATIONS),
    (r"(?<!미)귀속|(?<!un)attributed|채택|adoption|고유|unique|중복\s*제거", ADOPTION - {"unattributed_using_papers", "before_claim"}),
    (r"이전|before[_ ]claim", {"before_claim"}), (r"학회(?!\s*(?:전체|에서|별))|venues?", {"count:venues"}),
    (r"파일|(?<![A-Za-z])files?(?![A-Za-z])", {"n_files"})]
_GENERIC_WORDS = [(r"사용|(?<![A-Za-z])uses?(?![A-Za-z])|usage|관측|observations?", USAGE)]
_CONTEXT_WORDS = [(r"타\s*저자|다른\s*저자|(?<![A-Za-z])others?(?![A-Za-z])", "group", "others"), (r"자기|자체\s*저자|(?<![A-Za-z])self(?![A-Za-z])", "group", "self"),
                  (r"미확정|(?<![A-Za-z])undetermined(?![A-Za-z])", "group", "undetermined"), (r"평가|evaluat", "role", "evaluates_on"), (r"학습|훈련|train", "role", "trains_on")]


def fold(name: str) -> str:
    """Case, spaces, hyphens and underscores only: '+' and other identity characters stay ('HumanEval' != 'HumanEval+')."""
    return re.sub(r"[\s_-]", "", name.lower())


def _norm_url(u) -> str:
    u = str(u or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return re.sub(r"(?:\.git)?/*$", "", u)


_CORRECTED_WORDS = re.compile(r"보정|고친|고쳐|corrected|repaired", re.I)
_PRINTED_WORDS = re.compile(r"원래|인쇄|논문에\s*(?:적힌|실린|나온)|printed|original", re.I)


def _link_cues(text: str, a: int, b: int, fs: list[dict]) -> dict:
    """For a file count: the address (printed or corrected) and the link the claim's own line names, nearest
    before the number first. Nothing is guessed: a cue that is not there stays out."""
    line_start = text.rfind("\n", 0, a) + 1
    line_end = text.find("\n", b)
    line_end = len(text) if line_end < 0 else line_end
    low = text[line_start:line_end].lower()
    at = a - line_start
    cues: dict = {}
    words = [(m.end(), "corrected") for m in _CORRECTED_WORDS.finditer(low[:at])] + \
            [(m.end(), "printed") for m in _PRINTED_WORDS.finditer(low[:at])]
    if words:
        cues["address"] = max(words)[1]
    urls = {f["ctx"]["link_url"] for f in fs if f["metric"] == "n_files" and f["ctx"].get("link_url")}
    seen = []
    for u in urls:
        for form in {u, u.split("/", 1)[1] if "/" in u else u}:
            for m in re.finditer(re.escape(form), low):
                seen.append((m.start(), u))
    before = [x for x in seen if x[0] < at]
    if before:
        cues["link_urls"] = {max(before)[1]}
    elif seen:
        cues["link_urls"] = {min(seen)[1]}
    return cues


def _call_editions(result, in_scope: bool = False) -> tuple | None:
    """The editions one call covers, from its coverage (or, for benchmark_scope, its own edition list). With
    `in_scope`, only those with in-scope papers: an edition with none adds nothing to a value pooled in scope
    (a field filter's latest editions where only three venues carry the label)."""
    if not isinstance(result, dict):
        return None
    for o in (result.get("coverage"), result):
        eds = o.get("editions") if isinstance(o, dict) else None
        if isinstance(eds, dict) and eds and all(_EDITION.fullmatch(str(k)) for k in eds):
            if not in_scope:
                return tuple(sorted(str(k).lower() for k in eds))
            counts = [(str(k).lower(), (x or {}).get("in_scope_parsed") if isinstance(x, dict) else None) for k, x in eds.items()]
            if not all(isinstance(n, int) and not isinstance(n, bool) for _, n in counts):
                return None                        # no in-scope count for every edition: no support set
            return tuple(sorted(k for k, n in counts if n > 0))
    return None


def facts(log: TurnLog) -> list[dict]:
    """(value, metric, context) for every number in the turn's benchmark tool results. A value inside one
    benchmark's result carries its id, except the scope-wide unresolved-observation diagnostic.
    That diagnostic also spans all roles, regardless of the query's selected role."""
    out: list[dict] = []

    def link_facts(links, c):
        """each file count belongs to ONE link and ONE address: the printed one or its repair"""
        out.append({"value": float(len(links)), "metric": "count:paper_links", "ctx": c})
        for link in links:
            if not isinstance(link, dict):
                continue
            if isinstance(link.get("n_files"), (int, float)) and not isinstance(link["n_files"], bool):
                out.append({"value": float(link["n_files"]), "metric": "n_files",
                            "ctx": {**c, "link_url": _norm_url(link.get("url")), "address": "printed"}})
            fixed = link.get("corrected") or {}
            if isinstance(fixed.get("n_files"), (int, float)) and not isinstance(fixed["n_files"], bool):
                out.append({"value": float(fixed["n_files"]), "metric": "n_files",
                            "ctx": {**c, "link_url": _norm_url(fixed.get("url")), "address": "corrected"}})

    def walk(o, ctx, key, denominator=None):
        if isinstance(o, dict):
            c = dict(ctx)
            if isinstance(o.get("id"), str) and isinstance(o.get("name"), str):
                c["benchmark_id"] = o["id"]
                c["surfaces"] = {o["id"], o["name"], *[a for a in o.get("aliases") or [] if isinstance(a, str)]}
            if isinstance(o.get("edition"), str):
                c["edition"] = o["edition"].lower()
            if isinstance(o.get("role"), str) and o["role"] in _ROLE_KEYS:
                c["role"] = o["role"]
            # Usage rows share ONLY their own call's denominator. Trend rows
            # carry their edition's denominator; never borrow from another row/call.
            if c.get("tool") == "benchmark_usage" and isinstance(o.get("coverage"), dict):
                denominator = o["coverage"].get("parsed_papers")
            n = o.get("parsed_papers") if "parsed_papers" in o else denominator if c.get("tool") == "benchmark_usage" else None
            p = o.get("papers")
            has_percentage = (c.get("tool") in {"benchmark_usage", "benchmark_trend"} and c.get("benchmark_id")
                              and isinstance(p, (int, float)) and not isinstance(p, bool)
                              and isinstance(n, (int, float)) and not isinstance(n, bool) and n > 0)
            if has_percentage:
                # Also works on saved pre-percentage tool logs. Use counts,
                # not the rounded per_1000 value or arbitrary pooled values.
                out.append({"value": percentage(p, n, places=None), "metric": "share_percent", "ctx": dict(c)})
            if key in {"editions", "by_edition", "by_year"} or (o and all(_EDITION.fullmatch(str(k)) for k in o)):
                out.append({"value": float(len(o)), "metric": f"count:{key}" if key else "count:editions", "ctx": c})
            for k, v in o.items():
                if k == "share_percent" and has_percentage:
                    continue                           # the exact ratio above avoids rounding a share twice
                if k == "introducing_paper" and isinstance(v, dict) and v.get("links"):
                    link_facts(v["links"], dict(c))                     # the paper's links: their file counts are facts
                    continue
                if k == "other_paper_links" and isinstance(v, list):
                    link_facts(v, dict(c))
                    continue
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
                walk(v, cc, k, denominator)
        elif isinstance(o, list):
            if key:
                out.append({"value": float(len(o)), "metric": f"count:{key}", "ctx": ctx})
            for v in o:
                walk(v, ctx, key, denominator)
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            if key in UNATTRIBUTED_OBSERVATIONS:
                # Coverage spans all benchmarks and roles, even when nested
                # in a single-benchmark or evaluation/training result.
                ctx = {k: v for k, v in ctx.items() if k not in {"benchmark_id", "surfaces", "role"}}
            out.append({"value": float(o), "metric": key or "", "ctx": ctx})

    for e in log.entries:
        if RECOMPUTABLE.get(e.get("name")) != "json":
            continue
        base = {"tool": e.get("name"), "args": canonical_args(e.get("arguments") or {}), "revision": e.get("revision"),
                # a value with no edition of its own is pooled over the editions this call covers; benchmark_scope
                # describes the installed data, every other call one query's scope
                "edition_set": _call_editions(e.get("result")), "edition_support": _call_editions(e.get("result"), in_scope=True),
                "scope": "installed" if e.get("name") == "benchmark_scope" else "query"}
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


_TOTAL_MEASURES = {"total_papers", "parsed_papers", "in_scope_parsed", "total", "catalogue_entries"}


def _metric_cues(before: str, after: str) -> set | None:
    """What a number counts, from its unit and the words before it. "__total__": only "전체"/"total" says so, and a
    total of listed, analyzed or in-scope papers is not told apart."""
    r = _metric_words(before, after)
    if r and len(r & _TOTAL_MEASURES) > 1:
        best = None
        for rx, fam in _SPECIFIC_WORDS:
            for m in re.finditer(rx, before, re.I):
                if best is None or m.end() > best[0]:
                    best = (m.end(), fam)
        if best and "__total__" in best[1]:
            r = r | {"__total__"}
    return r


def _metric_words(before: str, after: str) -> set | None:
    if re.search(r"(?:(?<![A-Za-z])(?:those|these)|이\s*중|그\s*중|해당)\s*$", before, re.I):
        # "Of those 513 papers": a figure stated before, restated; which one is not said here
        return {"__generic__"} | set(USAGE) | set(DENOM)
    unit = next((set(fam) for rx, fam in _UNIT_AFTER if re.match(rx, after, re.I)), None)
    if unit == PERCENT:
        return set(PERCENT)                            # a percent is never a paper count or a per-1,000 rate
    # Korean pairs run whole-then-part: "2,296편 중 1,894편". The whole is a denominator or listed total; the part
    # is a use count or an analyzed count, whichever the edition's own values hold (never a third measure).
    if re.match(r"\s*편\s*중\s*\d", after):
        # a measure word right before the whole is the whole's ("수록 논문 5,468편 중"); one that ends in a topic
        # ("분석된 논문 수는 2,296편 중 1,894편") is the part's. Otherwise the part decides (see _pairs)
        adjacent = re.search(r"(수록|listed|분석|파싱|analy[sz]ed|parsed|매칭|matched)\S{0,2}\s*(?:논문\s*)?$", before, re.I)
        if adjacent:
            w = adjacent.group(1).lower()
            return {"total_papers"} if w in ("수록", "listed") else {"arxiv_matched"} if w in ("매칭", "matched") else {"parsed_papers", "in_scope_parsed"}
        return set(DENOM) | {"__whole__"}
    if re.search(r"\d\s*편\s*중\s*$", before) and not re.search(r"(?:분모|파싱|분석|parsed|analy[sz]ed|매칭|matched)", before.split("중")[-1], re.I):
        return set(USAGE) | {"parsed_papers", "in_scope_parsed", "arxiv_matched"}
    if unit is None and re.match(r"\s*(?:/|of(?![A-Za-z]))\s*\d", after) and not re.search(r"(?:분모|파싱|parsed|매칭|matched)\s*$", before, re.I):
        return set(USAGE)                                      # "0/547", "82 of 513": an edition's use count ...
        # ... unless a word right before says what it is ("parsed 1894 of 2296 accepted")
    if (unit is None or unit == _COUNTS) and re.search(r"\d\s*(?:/|(?<![A-Za-z])of)\s*$", before):
        return set(DENOM) | {"__whole__"}                      # ... over its denominator ("13 of 237 papers"), which the part pins down
    best = None
    for rx, fam in _SPECIFIC_WORDS:
        for m in re.finditer(rx, before, re.I):
            if best is None or m.end() > best[0]:
                best = (m.end(), fam)
    word = set(best[1]) - {"__total__"} if best else None
    if word and word <= EDITIONS and re.match(r"\s*(?:건|편|papers?(?![A-Za-z]))", after):
        word = None                                    # "세 에디션을 합쳐 4,185건": an edition word does not say what 4,185 items are
    if re.match(r"\s*(?:건|편|papers?)?\s*(?:의\s*)?(?:논문[-\s]?에디션\s*관측|paper-edition\s+observations?)\S*\s*(?:에\s*)?(?:해결되지\s*않은|미해소|미귀속|unresolved)", after, re.I) \
            or re.match(r"\s*(?:papers?|paper-edition\s+observations?)\s+with\s+(?:an?\s+|at\s+least\s+one\s+)?(?:unresolved|unattributed)", after, re.I):   # the clause after a number is cut at 30 characters
        return set(UNATTRIBUTED_OBSERVATIONS)          # "4,185건의 논문-에디션 관측에 해결되지 않은 … 언급": the scope diagnostic
    if word is None:
        for rx, fam in _GENERIC_WORDS:
            if re.search(rx, before, re.I):
                return {"__generic__"} | (set(fam) & unit if unit else set(fam))   # a guess: may connect, never contradicts
    if unit is None:
        return word
    if word is None:
        return unit
    if word == {"n_files"}:
        return word                                             # "파일 96개": the word says what the count is
    if word & UNATTRIBUTED_OBSERVATIONS and unit in (_COUNTS, _ANY):
        return word                                             # paper-edition diagnostic, not adoption by the "papers" unit
    narrowed = {x for x in word if x in unit or ("count:*" in unit and x.startswith("count:"))}
    if narrowed:
        return narrowed
    # a denominator or total word with a count unit ("분모 3,059편", "전체 4,417편", "미라벨 35,504건"): the word says what is counted
    return set(word) if set(word) <= TOTALS and (unit == _COUNTS or unit == _ANY) else unit


_ED_RX = r"(?<![a-z])(" + "|".join(_VENUES) + r")[\s-]?((?:19|20)\d\d)(?!\d)"
_ED_SEP = r"\s*(?:,|、|·|&|(?<![a-z])and(?![a-z])|(?<![a-z])or(?![a-z])|및|와|과|그리고)\s*(?:and\s+)?"
_CLAUSE_BREAKS = ("\n", ". ", "。", "|", ";", ", ", "(", ")", " — ", " – ", ": ")


def _edition_run(low: str, at: int) -> tuple[set, int]:
    """An enumeration of editions starting at `at` ("ICLR 2026, ICML 2026 and NeurIPS 2025"): its editions and end."""
    m = re.compile(_ED_RX).match(low, at)
    eds, end = {f"{m.group(1)}-{m.group(2)}"}, m.end()
    nxt = re.compile(_ED_SEP + _ED_RX)
    while (r := nxt.match(low, end)):
        eds.add(f"{r.group(1)}-{r.group(2)}")
        end = r.end()
    return eds, end


def _editions_near(text: str, a: int, b: int, before: str, after: str) -> set:
    """The editions this number is stated for. Before it: those in its clause, with an enumeration cut by the
    clause's start read back to its first edition. After it: an enumeration that starts inside the clause, read
    to its end, unless another number follows it in the same clause (then the editions are that number's)."""
    low = text.lower()
    start = a - len(before)
    eds: set = set()
    own: set = set()
    for m in re.finditer(_ED_RX, before.lower()):
        ed = f"{m.group(1)}-{m.group(2)}"
        eds.add(ed)
        if not re.search(r"\d", re.sub(_ED_RX, " ", before.lower()[m.end():])):
            own.add(ed)                            # no other number between this edition and this number
    if own and own != eds:
        eds = set(own)                             # "ICLR 2026 has 183 …, ICML 2026 has 167": ICLR 2026 is the 183's
    if eds and re.match(r"\s*" + _ED_RX, before.lower()):
        p = start
        while (r := re.search(_ED_RX + r"\s*(?:,|、|·)\s*$", low[:p])):
            eds.add(f"{r.group(1)}-{r.group(2)}")  # "ICLR 2026, ICML 2026 and NeurIPS 2025 together have 513"
            p = r.start()
    window = b + len(after)
    sentence_end = min([i for i in (text.find(ch, b) for ch in ("\n", ". ", "。", "|", ";")) if i >= 0] or [len(text)])
    after_eds: set = set()
    pos = b
    while (m := re.compile(_ED_RX).search(low, pos, sentence_end)) and m.start() < window:
        run, end = _edition_run(low, m.start())
        clause_end = min([i for i in (text.find(ch, end) for ch in _CLAUSE_BREAKS) if i >= 0] or [len(text)])
        if not re.search(r"\d", re.sub(_ED_RX, " ", low[end:min(clause_end, sentence_end)])):
            after_eds |= run                        # "513 parsed papers across ICLR 2026, ICML 2026 and NeurIPS 2025."
        pos = end
    return eds | after_eds


def _context_cues(before: str, after: str, text: str | None = None, a: int | None = None, b: int | None = None) -> dict:
    right_after = re.split(r"\d", after, maxsplit=1)[0][:12]
    c: dict = {}
    for rx, kind, val in _CONTEXT_WORDS:
        if re.search(rx, before + " " + right_after, re.I):
            c[kind] = val
    if text is not None:
        eds = _editions_near(text, a, b, before, after)
    else:
        eds = {f"{v}-{y}" for v, y in re.findall(_ED_RX, before.lower())}
        # after the number, an edition followed by another number is that number's ("…before_claim 0
        # 에디션별로는 neurips-2025에 타 저자 3편": neurips-2025 is the 3's); a trailing one is this one's
        low = after.lower()
        for m in re.finditer(_ED_RX, low):
            if not re.search(r"\d", re.sub(_ED_RX, " ", low[m.end():])):
                eds.add(f"{m.group(1)}-{m.group(2)}")
    if eds:
        c["edition"] = eds
    scope = _scope_cue(before + " " + right_after)
    if scope:
        c["scope"] = scope
    return c


_INSTALLED_WORDS = re.compile(r"설치(?:된|돼\s*있는|되어\s*있는)?\s*(?:데이터|전체|코퍼스)|(?<![A-Za-z])installed(?![A-Za-z])|전체\s*데이터|데이터\s*전체|"
                              r"(?:whole|entire|full)\s+(?:corpus|data(?:set|base)?)|(?<![A-Za-z])corpus(?![A-Za-z])|코퍼스", re.I)
_QUERY_WORDS = re.compile(r"이번\s*(?:조회|질의|검색|조건)|조회\s*(?:범위|결과)|(?:this|the)\s+(?:query|lookup|search)|"
                          r"(?<![A-Za-z])filter(?:ed)?(?![A-Za-z])|필터", re.I)


def _scope_cue(words: str) -> str | None:
    """Installed data (benchmark_scope) or one query's scope; the nearer word wins, none gives no cue."""
    found = [(m.end(), "installed") for m in _INSTALLED_WORDS.finditer(words)] + [(m.end(), "query") for m in _QUERY_WORDS.finditer(words)]
    return max(found)[1] if found else None


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
            names = [j for j, h in enumerate(header) if re.search(r"benchmark|벤치마크|dataset|데이터셋|(?<![A-Za-z])name(?![A-Za-z])|이름", h, re.I)]
            # a header that also says what it counts ("Papers evaluating on the benchmark") is a figure column
            counted = {j for j in names if re.search(r"papers(?![A-Za-z])|편|논문\s*수|(?<![A-Za-z])counts?(?![A-Za-z])|share|(?<![A-Za-z])rate|per\s*1,?000|‰|%|"
                                                      r"(?<![A-Za-z])uses(?![A-Za-z])|사용", header[j], re.I)}
            ed_col = next((j for j, h in enumerate(header) if re.search(r"edition|회차|에디션|venue|학회", h, re.I)), None)
            ident = next((j for j in names if j not in counted), None)
            if ident is None and ed_col is None and names:
                ident = names[0]                                # no other identity: as before
            k = i + 2
            while k < len(lines) and lines[k].strip().startswith("|"):
                cells, start = [], offs[k] + lines[k].index("|") + 1
                for part in lines[k].strip().strip("|").split("|"):
                    cells.append((start, start + len(part), part)); start += len(part) + 1
                out.append({"span": (offs[k], offs[k] + len(lines[k])), "header": header, "cells": cells, "top": offs[i],
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
        if not (ctx.get("edition") is None and any(ctx.get(k) and set(ctx[k]) == c["edition"] for k in ("edition_set", "edition_support"))):
            return False                               # a pooled value fits only the very editions it was pooled over
    if "scope" in c and ctx.get("scope") != c["scope"]:
        return False                                   # the installed data (benchmark_scope) or one query's scope
    if "role" in c and ctx.get("role") != c["role"]:
        return False
    if "group" in c and ctx.get("group") != c["group"]:
        return False
    if "address" in c and ctx.get("address") != c["address"]:
        return False                                   # the printed address's count is never confirmed by its repair's
    if "link_urls" in c and ctx.get("link_url") not in c["link_urls"]:
        return False
    return True


def _call(f: dict) -> tuple:
    return (f["ctx"].get("tool"), f["ctx"].get("args"))


def _judge(raw: str, v: float, c: dict, fs: list[dict]) -> tuple[str, str]:
    return _judge_full(raw, v, c, fs)[:2]


def _judge_full(raw: str, v: float, c: dict, fs: list[dict], within=None) -> tuple[str, str, list, list]:
    """(verdict, why, hits, candidates). `within`, when given, admits only the facts it accepts and replaces the
    benchmark test: a pair's denominator or share is judged inside its part's own lookup (see _pairs)."""
    dp = len(raw.split(".")[1]) if "." in raw else 0
    flags = {x for x in (c.get("metric") or ()) if x.startswith("__")}
    if flags:
        c = {**c, "metric": c["metric"] - flags}
    guessed, whole, total = "__generic__" in flags, "__whole__" in flags, "__total__" in flags
    if c.get("ambiguous_name") and within is None:
        return "na", "the name means several benchmark ids", [], []
    if not (any(k in c for k in ("role", "group")) or ("metric" in c and c["metric"] != _ANY)):
        return "na", "no cue says what it counts", [], []
    if within is not None:
        scoped = {k: x for k, x in c.items() if k != "benchmark_ids"}
        cand = [f for f in fs if within(f) and _satisfies(f, scoped)]
    else:
        cand = [f for f in fs if _satisfies(f, c)]
    if c.get("benchmark_ids") is None and "metric" in c and c["metric"] <= _SCOPE_WIDE:
        cand = [f for f in cand if not f["ctx"].get("benchmark_id")]   # "3 editions" with no benchmark: never one benchmark's subset
    denominators = set(c.get("metric") or ()) & DENOM if (c.get("metric") and (c["metric"] <= DENOM or guessed)) else set()
    named_denominator = within is None and bool(c.get("benchmark_ids")) and bool(denominators)   # a restated figure may be one too
    if named_denominator:
        # "| LIBERO | 513 |" under an analyzed-papers column: the scope-wide denominator of a call that counted
        # this benchmark's use (the usage call's pooled 513); a call with no use count for it (evidence) never
        calls = {_call(f) for f in fs if f["ctx"].get("benchmark_id") in c["benchmark_ids"] and f["metric"] in USAGE}
        scoped = {**{k: x for k, x in c.items() if k != "benchmark_ids"}, "metric": denominators}
        cand += [f for f in fs if f["ctx"].get("benchmark_id") is None and _call(f) in calls
                 and _satisfies(f, scoped) and f not in cand]
    if cand and c.get("metric") == {"n_files"}:
        if len({(f["ctx"].get("link_url"), f["ctx"].get("address")) for f in cand}) > 1:
            return "na", "which link or address the file count is for is not stated", [], cand
        if "address" not in c and any(f["ctx"].get("address") == "corrected" for f in cand):
            return "na", "the printed or the corrected address is not stated", [], cand
    fams = {family(f["metric"]) for f in cand}
    if {"usage", "adoption"} <= fams and "metric" in c and c["metric"] >= _COUNTS:
        return "na", "usage (paper-edition observations) or adoption (unique papers) is not stated", [], cand
    if {"unattributed_observations", "adoption"} <= fams:
        return "na", "scope-wide unresolved observations or a benchmark's unique unattributed papers is not stated", [], cand

    def rounded(value):
        if c.get("metric") and c["metric"] <= PERCENT:
            return float(Decimal(str(value)).quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP))
        return round(value, dp)
    if total and len({rounded(f["value"]) for f in cand if f["metric"] in _TOTAL_MEASURES}) > 1:
        return "na", "a total, but whether of listed, analyzed or in-scope papers is not stated", [], cand
    hits = [f for f in cand if rounded(f["value"]) == rounded(v)]
    benchmark_stat = any(family(f["metric"]) not in ("denominator", "editions") for f in hits)
    if c.get("benchmark_ids") is None and within is None and benchmark_stat \
            and len({f["ctx"].get("benchmark_id") for f in hits if f["ctx"].get("benchmark_id")}) > 1 \
            and all(f["ctx"].get("benchmark_id") for f in hits):
        return "na", "the value belongs to several benchmarks and the text names none", [], cand
    if hits:
        return "ok", "connected", hits, cand
    if cand and guessed:
        return "na", "only a generic word says what it counts", [], cand
    if whole:
        return "na", "the whole of a pair can be a listed or an analyzed total", [], cand
    if named_denominator and len({_call(f) for f in cand}) > 1:
        return "na", "several lookups returned a denominator for this benchmark; which one is not stated", [], cand
    if len(c.get("edition") or ()) > 1:
        return "na", "several editions are named: a pooled value or one edition's is not stated", [], cand
    # a contradiction needs a fixed context: what is counted, and for which benchmark or edition (a scope-wide
    # measure such as the number of editions needs neither). "ICLR 2026 (183 papers)" names neither inside its
    # clause, so a different value there is unverified, never contradicted.
    if cand and not (c.get("benchmark_ids") or "edition" in c or within is not None or ("metric" in c and set(c["metric"]) <= _SCOPE_WIDE)):
        return "na", "which benchmark or edition this number is for is not stated", [], cand
    if cand and within is None and not c.get("benchmark_ids") and all(f["ctx"].get("benchmark_id") for f in cand):
        return "na", "the values in this context are a benchmark's, and the text names none", [], cand
    if cand:
        vals = sorted({rounded(f["value"]) for f in cand})[:4]
        return "no", f"the tool results for this context say {', '.join(f'{x:g}' for x in vals)}", [], cand
    return "na", "no tool result has this context", [], cand


def scan_connected(text: str, fs: list[dict], date_strings: set[str], skip: set) -> list[tuple]:
    """(start, end, raw, verdict, why): 'ok' connected, 'no' contradicted
    (facts with these cues hold another value), 'na' not connected, 'date-ok' /
    'date-no' for a date found / not found verbatim in this turn's results.
    Dates are not statistics: they are never counted as verified figures."""
    text = text.translate(_MINUS)
    surf, raw_names = _surfaces(fs), None
    tables = _tables(text)
    out, taken, judged = [], [], []
    for m in _DATE.finditer(text):
        raw = m.group(0)
        out.append((m.start(), m.end(), raw, "date-ok" if raw[:10] in date_strings else "date-no", "date"))
        taken.append((m.start(), m.end()))
    month_days = {d[5:10] for d in date_strings}
    for m in re.finditer(r"(?<![\d.,:/-])(\d{1,2})-(\d{1,2})(?![\d])", text):
        if f"{int(m.group(1)):02d}-{int(m.group(2)):02d}" not in month_days or any(a <= m.start() < b for a, b in taken):
            continue
        pre, post = text[max(0, m.start() - 16):m.start()], text[m.end():m.end() + 12]
        if re.match(r"\s*(?:papers?|편|개|건|회|명|uses?|files?|%)", post, re.I):
            continue                                            # "10-21 papers": a range of counts, never a date
        if re.search(r"(?:시각|날짜|일자|확인일|checked(?:\s+on)?|dated?|as\s+of)\s*[(:]?\s*$", pre, re.I) or re.match(r"\)?\s*(?:UTC|기준)", post):
            out.append((m.start(), m.end(), m.group(0), "date-ok", "date"))    # "확인 시각(10-21)": a returned date, shortened
            taken.append((m.start(), m.end()))
    for m in _NUM.finditer(text):
        if any(a <= m.start() < b for a, b in taken) or _identifier_number(text, m):
            continue
        if re.search(r"HTTP\s*$", text[max(0, m.start() - 6):m.start()]):
            continue                                            # an HTTP status code, not a figure
        if re.match(r"\s*(?:percentage\s+points?|percent\s+points?|%p\b|퍼센트\s*포인트)", text[m.end():], re.I):
            out.append((m.start(), m.end(), m.group(0), "na", "a percentage-point change is not a usage share"))
            continue
        if not _YEAR.fullmatch(m.group(0).lstrip("-")) and (re.match(r"\s*(?:–|—|~|-(?=\d))\s*\d", text[m.end():m.end() + 4]) or re.search(r"\d\s*(?:–|—|~)\s*$", text[max(0, m.start() - 4):m.start()])
                or (m.group(0).startswith("-") and re.search(r"\d$", text[max(0, m.start() - 1):m.start()]))):
            out.append((m.start(), m.end(), m.group(0), "na", "a range, not a stated value"))
            continue                                            # "roughly 60–90%": neither end is a reported value
        if re.search(r"(?:more|fewer|less)\s+than\s*$|at\s+(?:least|most)\s*$|[<>≤≥]=?\s*$", text[max(0, m.start() - 14):m.start()], re.I) \
                or re.match(r"\s*(?:편|개|건)?\s*(?:이상|이하|초과|미만)", text[m.end():m.end() + 8]):
            out.append((m.start(), m.end(), m.group(0), "na", "a comparison, not a stated value"))
            continue                                            # "more than 4": not judged as "exactly 4"
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
            before, after = head + " " + text[cell[1][0]:m.start()], text[m.end():cell[1][1]]   # "82 of 513": the cell's own words count
            c = {"metric": _metric_cues(before, after)} if _metric_cues(before, after) else {}
            c.update(_context_cues(head, after))
            words = re.split(r"\d", after, maxsplit=1)[0]          # the cell's own words up to its next number
            own = next((val for rx, kind, val in _CONTEXT_WORDS if kind == "group" and re.search(rx, words, re.I)), None)
            if own:                                             # "5 other / 0 self": the cell's own word beats a header naming every group
                c["group"], c["metric"] = own, set(ADOPTION) - {"unattributed_using_papers", "before_claim"}
            elif re.match(r"\s*(?:unresolved|unattributed|미귀속|미해소)", words, re.I):
                c.pop("group", None)
                c["metric"] = {"unattributed_using_papers"} | UNATTRIBUTED_OBSERVATIONS
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
            elif row["ident_col"] is None:
                above = text[:row["top"]].rstrip("\n").rsplit("\n", 1)[-1]   # "ToyBench at ICLR:" introducing the table
                ids = _mentions(above, surf) if above.strip() and not above.lstrip().startswith("|") else None
                ids = ids or _section(text, row["top"], surf)
                if ids is not None:
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
            c.update(_context_cues(before, after, text, m.start(), m.end()))
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
        if c.get("metric") == {"n_files"}:
            c.update(_link_cues(text, m.start(), m.end(), fs))   # which link, which address: from the claim's own line
        verdict, why, hits, cand = _judge_full(raw, v, c, fs)
        judged.append({"i": len(out), "a": m.start(), "b": m.end(), "raw": raw, "v": v, "c": c, "hits": hits, "cand": cand})
        out.append((m.start(), m.end(), raw, verdict, why))
    _pairs(text, judged, out, fs)
    return sorted(out)


def _same_lookup(anchors: list[dict]):
    """A fact of the same call and the same pooled or per-edition scope as one of `anchors`, for its benchmark or none."""
    keys = {(_call(x), _editions_of(x), x["ctx"].get("benchmark_id")) for x in anchors}
    return lambda f: any((_call(f), _editions_of(f)) == k[:2] and f["ctx"].get("benchmark_id") in (None, k[2]) for k in keys)


def _editions_of(f: dict) -> tuple | None:
    """The editions a value covers: its own, or those its call pooled over (one edition pooled is that edition)."""
    return (f["ctx"]["edition"],) if f["ctx"].get("edition") else f["ctx"].get("edition_set")   # the call's whole set: support is a claim-side match only


def _pairs(text: str, judged: list[dict], out: list, fs: list[dict]) -> None:
    """A ratio is one claim: "82 of 513 papers (16.0%)", "4,252편 중 82편(1.9%)". Its whole and its share are
    re-judged inside the lookup the part connected to (or, for a wrong part, the lookup that has the part's
    context), so a part, whole and share are never confirmed from three different calls. When the part connects
    nowhere, a whole that only "of"/"중" calls a whole says nothing checkable."""
    order = sorted(judged, key=lambda x: x["a"])
    for k in range(len(order) - 1):
        x, y = order[k], order[k + 1]
        gap = text[x["b"]:y["a"]]
        if re.fullmatch(r"\s*(?:/|of(?![A-Za-z]))\s*", gap):
            part, whole = x, y
        elif re.fullmatch(r"\s*편\s*중\s*", gap):
            whole, part = x, y
        else:
            continue
        last = max(part, whole, key=lambda z: z["a"])
        share = order[k + 2] if k + 2 < len(order) else None
        if share is not None and not (re.fullmatch(r"\s*(?:(?:analy[sz]ed|parsed|listed)\s+)?(?:papers?|편)?\s*(?:이|가)?\s*[(,:]?\s*(?:or\s+|즉\s*)?",
                                                   text[last["b"]:share["a"]]) and re.match(r"\s*%", text[share["b"]:])):
            share = None
        _settle(part, whole, share, out, fs)


def _settle(part: dict, whole: dict, share: dict | None, out: list, fs: list[dict]) -> None:
    verdict = out[part["i"]][3]
    anchors = part["hits"] if verdict == "ok" else part["cand"] if verdict == "no" else []
    wm = set(whole["c"].get("metric") or ())
    implied = "__whole__" in wm                        # only "of"/"중" says it is a whole: the part says of what
    wm = {x for x in wm if not x.startswith("__")}
    a, b, raw = out[whole["i"]][:3]
    if not anchors:
        if implied:
            out[whole["i"]] = (a, b, raw, "na", "the part of this pair is not connected, so what its whole counts is not stated")
        return
    fams = {family(f["metric"]) for f in anchors}
    if fams & {"usage", "adoption"}:
        allowed = {"parsed_papers", "in_scope_parsed"} | (set(USAGE) if "adoption" in fams else set())
    elif fams == {"denominator"}:
        allowed = set()
        for f in anchors:
            allowed |= {"total_papers"} if f["metric"] == "arxiv_matched" else \
                {"total_papers", "arxiv_matched", "parsed_papers"} if f["metric"] in ("parsed_papers", "in_scope_parsed") else set()
    else:
        allowed = None
    metric = (wm & allowed if allowed is not None else wm) if implied else wm
    if not metric:
        out[whole["i"]] = (a, b, raw, "na", "a whole of this measure does not fit the part's measure")
        return
    if fams == {"denominator"} and all(f["ctx"].get("edition") for f in anchors):
        eds = {f["ctx"]["edition"] for f in anchors}       # one edition's coverage is the same in every lookup
        within = lambda f: f["ctx"].get("edition") in eds
    else:
        within = _same_lookup(anchors)
    wv, why, hits, _ = _judge_full(raw, whole["v"], {**whole["c"], "metric": metric}, fs, within)
    out[whole["i"]] = (a, b, raw, wv, why)
    if share is None:
        return
    base = [f for f in anchors if wv != "ok" or any(_call(f) == _call(h) for h in hits)]
    sa, sb, sraw = out[share["i"]][:3]
    sv, swhy, _, _ = _judge_full(sraw, share["v"], {**share["c"], "metric": set(PERCENT)}, fs,
                                 lambda f, inside=_same_lookup(base): inside(f) and f["ctx"].get("benchmark_id") is not None)
    out[share["i"]] = (sa, sb, sraw, sv, swhy if sv != "na" or swhy != "no tool result has this context"
                       else "the share is not in the lookup its count came from")


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
