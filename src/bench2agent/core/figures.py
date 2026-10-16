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

# metric families; a fact's metric is its key ("count:<key>" for a list's or a mapping's size)
_PAPERS = {"papers", "parsed_papers", "total_papers", "arxiv_matched", "in_scope_parsed", "field_labelled_papers", "unattributed_papers",
           "others", "self", "undetermined", "unattributed_using_papers", "before_claim", "total", "n_papers", "using_papers", "sum:groups"}
_ANY_COUNT = _PAPERS | {"count:*", "catalogue_entries"}
_EDITIONS = {"count:editions", "count:by_edition", "editions", "n_editions"}
_RATE = {"per_1000", "share_per_1000", "rate_per_1000"}
# the unit right after a number sets its family
_UNIT_AFTER = [(r"\s*(?:개\s*)?(?:회차|에디션|editions?(?![A-Za-z]))", _EDITIONS), (r"\s*(?:편|papers?(?![A-Za-z])|논문)", _PAPERS),
               (r"\s*(?:‰|/\s*1,?000(?!\d)|per\s*1,?000(?!\d)|(?:편|개)?\s*당\s*1,?000(?!\d)|/\s*천)", _RATE), (r"\s*%", {"percent"}),
               (r"\s*(?:건|개|items?(?![A-Za-z]))", _ANY_COUNT)]
# the nearest of these words BEFORE the number narrows the family
_METRIC_WORDS = [(r"(?<!논문-)(?<!paper-)(?:회차|에디션|editions?)", _EDITIONS), (r"논문-에디션(?:\s*관측)?|paper-edition(?:\s*observations?)?", {"parsed_papers", "in_scope_parsed"}), (r"‰|per\s*1,?000|1,?000\s*(?:편|개)?\s*당|천\s*편당", _RATE),
                 (r"(?<![A-Za-z])z(?![A-Za-z])|z-?값", {"z"}), (r"분모|파싱|parsed", {"parsed_papers", "in_scope_parsed"}), (r"매칭|matched", {"arxiv_matched"}),
                 (r"전체|(?<![A-Za-z])total(?![A-Za-z])|총", {"total_papers", "total", "catalogue_entries"}), (r"미귀속|미해소|unattributed|unresolved", {"unattributed_using_papers", "unattributed_papers"}),
                 (r"채택|adoption", {"sum:groups", "others", "self", "undetermined"}),
                 (r"(?<!미)귀속|attributed", {"sum:groups", "others", "self", "undetermined"}), (r"미라벨|라벨\s*없|unlabell?ed", {"unattributed_papers", "unlabelled_papers"}), (r"(?<!미)라벨(?!\s*없)|(?<!un)labell?ed", {"field_labelled_papers"}),
                 (r"학회|venues?", {"count:venues"}), (r"이전|before[_ ]claim", {"before_claim"})]
_CONTEXT_WORDS = [(r"타\s*저자|다른\s*저자|(?<![A-Za-z])others?(?![A-Za-z])", "group", "others"), (r"자기|자체\s*저자|(?<![A-Za-z])self(?![A-Za-z])", "group", "self"),
                  (r"미확정|(?<![A-Za-z])undetermined(?![A-Za-z])", "group", "undetermined"), (r"평가|evaluat", "role", "evaluates_on"), (r"학습|훈련|train", "role", "trains_on")]


def facts(log: TurnLog) -> list[dict]:
    """(value, metric, context) for every number in the turn's benchmark tool results."""
    out: list[dict] = []

    def walk(o, ctx, key):
        if isinstance(o, dict):
            c = dict(ctx)
            if isinstance(o.get("id"), str) and isinstance(o.get("name"), str):
                c["benchmark"] = {o["id"].lower(), o["name"].lower()}
            if isinstance(o.get("edition"), str):
                c["edition"] = o["edition"].lower()
            if isinstance(o.get("role"), str) and o["role"] in _ROLE_KEYS:
                c["role"] = o["role"]
            if key in {"editions", "by_edition", "by_year"} or all(_EDITION.fullmatch(str(k)) for k in o) and o:
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
            out.append({"value": float(o), "metric": key or "", "ctx": ctx})

    for e in log.entries:
        if RECOMPUTABLE.get(e.get("name")) != "json":
            continue
        base = {"tool": e.get("name"), "args": canonical_args(e.get("arguments") or {}), "revision": e.get("revision")}
        args = e.get("arguments") or {}
        if isinstance(args, dict) and isinstance(args.get("role"), str):
            base["role"] = args["role"]
        walk(e.get("result"), base, None)
    # a share someone computes from a count and its denominator in ONE object (same context)
    by_ctx: dict = {}
    for f in out:
        k = json.dumps({x: (sorted(v) if isinstance(v, set) else v) for x, v in f["ctx"].items()}, sort_keys=True)
        by_ctx.setdefault(k, []).append(f)
    for fs in by_ctx.values():
        nums = [f for f in fs if f["metric"] in {"papers", "others", "self", "undetermined", "unattributed_using_papers", "n_papers"}]
        dens = [f for f in fs if f["metric"] in {"parsed_papers", "in_scope_parsed", "total_papers"}]
        for n in nums:
            for d in dens:
                if d["value"]:
                    out.append({"value": n["value"] / d["value"] * 100, "metric": "percent", "ctx": n["ctx"]})
        groups = [f for f in fs if f["metric"] in _GROUP_KEYS]
        if len(groups) >= 2:
            out.append({"value": sum(f["value"] for f in groups), "metric": "sum:groups", "ctx": {k: v for k, v in groups[0]["ctx"].items() if k != "group"}})
    return out


def _names(fs: list[dict]) -> set[str]:
    return {n for f in fs for n in (f["ctx"].get("benchmark") or ()) if len(n) >= 3}


def _clause(text: str, a: int, b: int) -> tuple[str, str]:
    """The number's own clause: back to the previous break, forward to the next."""
    start = max(text.rfind(ch, 0, a) for ch in ("\n", ". ", "。", "|", "; ", "·", "(", "—", ", ", " + ", "→"))
    start = 0 if start < 0 else start + 1
    ends = [i for i in (text.find(ch, b) for ch in ("\n", ". ", "|", ";", ", ", "(", ")", " + ", "—", " – ")) if i >= 0]
    return text[start:a], text[b:min(min(ends) if ends else len(text), b + 30)]


def _named(text: str, names: set[str]) -> set[str]:
    low = text.lower()
    return {n for n in names if re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", low)}


def _section(text: str, a: int, names: set[str]) -> set[str]:
    """The benchmark the number's section is about: the nearest heading or bold line above it naming one."""
    for line in reversed(text[:a].split("\n")[:-1]):
        if re.match(r"\s*(?:#{1,6}\s|[-*]?\s*\*\*)", line):
            hit = _named(line, names)
            if hit:
                return hit
    return set()


def cues(text: str, a: int, b: int, names: set[str]) -> dict:
    before, after = _clause(text, a, b)
    c: dict = {}
    for rx, fam in _UNIT_AFTER:
        if re.match(rx, after, re.I):
            c["metric"] = set(fam)
            break
    last = None
    for rx, fam in _METRIC_WORDS:
        for m in re.finditer(rx, before, re.I):
            if last is None or m.end() > last[0]:
                last = (m.end(), fam)
    if "metric" not in c and re.match(r"\s*/\s*\d", after):
        c["metric"] = {"papers", "sum:groups", "others", "self", "undetermined", "n_papers"}          # "0/547": a count ...
    if "metric" not in c and re.search(r"\d\s*/\s*$", before):
        c["metric"] = {"parsed_papers", "in_scope_parsed", "total_papers"}                            # ... over its denominator
        last = None
    if last:
        if "metric" in c:
            narrowed = {x for x in last[1] if x in c["metric"] or ("count:*" in c["metric"] and x.startswith("count:"))}
            c["metric"] = narrowed or c["metric"]
        else:
            c["metric"] = set(last[1])
    clause = before + " " + after
    right_after = re.split(r"\d", after, maxsplit=1)[0][:12]      # up to the next number, not into the next item
    for rx, kind, val in _CONTEXT_WORDS:               # roles and groups: before the number, or right after it
        if re.search(rx, before + " " + right_after, re.I):
            c[kind] = val
    hit = _named(clause, names)
    if hit:
        c["benchmark"] = hit
    else:
        sec = _section(text, a, names)
        if sec:
            c["section_benchmark"] = sec
    eds = {f"{v}-{y}" for v, y in re.findall(r"(?<![a-z])(" + "|".join(_VENUES) + r")[\s-]?((?:19|20)\d\d)(?!\d)", clause.lower())}
    if eds:
        c["edition"] = eds
    return c


def _specific(c: dict) -> bool:
    """A cue that says WHAT the number counts: a metric narrower than a generic
    count, or a role or author group. A benchmark or edition name alone says
    where to look, not what was counted."""
    return any(k in c for k in ("role", "group")) or ("metric" in c and c["metric"] != _ANY_COUNT)


def _fold(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _satisfies(f: dict, c: dict) -> bool:
    ctx, metric = f["ctx"], f["metric"]
    if "metric" in c and metric not in c["metric"] and not ("count:*" in c["metric"] and metric.startswith("count:")):
        return False
    named = c.get("benchmark") or c.get("section_benchmark")
    if named and ctx.get("benchmark") and not ({_fold(x) for x in ctx["benchmark"]} & {_fold(x) for x in named}):
        return False                                   # another benchmark's value ("Noisy MNIST" = "Noisy-MNIST")
    if "edition" in c and ctx.get("edition") not in c["edition"]:
        return False
    if "role" in c and ctx.get("role") != c["role"]:
        return False                                   # a role cue needs a fact that carries that role
    if "group" in c and ctx.get("group") != c["group"]:
        return False
    return True


def scan_connected(text: str, fs: list[dict], date_strings: set[str], skip: set) -> list[tuple]:
    """(start, end, raw, verdict, why): 'ok' connected, 'no' contradicted
    (facts with these cues hold another value), 'na' not connected, 'date-ok' /
    'date-no' for a date found / not found verbatim in this turn's results.
    Dates are not statistics: they are never counted as verified figures."""
    text = text.translate(_MINUS)
    names = _names(fs)
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
            continue                                            # "1. " / "### 2. " list numbering
        if text[max(0, m.start() - 1):m.start()] == "[" and text[m.end():m.end() + 1] == "]":
            continue                                            # [1] reference marker
        if v == 1000 and re.search(r"/\s*$", text[:m.start()]):
            continue                                            # the fixed rate unit in "0.0/1000"
        if text[m.end():m.end() + 1] == "차" and text[max(0, m.start() - 1):m.start()] != "회":
            continue                                            # "1차 출처": an ordinal, not a count
        if v in skip:
            continue
        if re.match(r"\s*(?:이|가)\s*(?:아니|아닙|아님)", text[m.end():m.end() + 6]) or re.search(r"\bnot\s+$", text[max(0, m.start() - 6):m.start()]):
            continue                                            # "…미확인이며 0이 아닙니다" / "not 0": a negation, not a reported value
        if _YEAR.fullmatch(raw) and (re.search(r"(?:19|20)\d\d\s*[/~–]\s*$", text[max(0, m.start() - 8):m.start()])
                                     or re.match(r"\s*[/~–]\s*(?:19|20)\d\d", text[m.end():m.end() + 8])):
            continue                                            # "eccv-2024/2026", "2020~2025": edition years, not counts
        c = cues(text, m.start(), m.end(), names)
        if _YEAR.fullmatch(raw) and "metric" not in c:
            continue                                            # a year as context, not a count (a year with a unit is checked)
        dp = len(raw.split(".")[1]) if "." in raw else 0
        if not _specific(c):
            out.append((m.start(), m.end(), raw, "na", "no cue in its clause connects it to a tool result"))
            continue
        cand = [f for f in fs if _satisfies(f, c)]
        hits = [f for f in cand if round(f["value"], dp) == round(v, dp)]
        # one product name shared by homonym entities with the same value is one answer, not an ambiguity
        owners = {frozenset(re.sub(r"[^a-z0-9]", "", n) for n in f["ctx"]["benchmark"]) for f in hits if f["ctx"].get("benchmark")}
        if owners and frozenset.intersection(*owners):
            owners = {frozenset.intersection(*owners)}
        if hits and not (c.get("benchmark") or c.get("section_benchmark")) and len(owners) > 1 and all(f["ctx"].get("benchmark") for f in hits):
            out.append((m.start(), m.end(), raw, "na", "the value belongs to several benchmarks and the text names none"))
        elif hits:
            out.append((m.start(), m.end(), raw, "ok", "connected"))
        elif cand:
            vals = sorted({round(f["value"], dp) for f in cand})[:4]
            out.append((m.start(), m.end(), raw, "no", f"the tool results for this context say {', '.join(f'{x:g}' for x in vals)}"))
        else:
            out.append((m.start(), m.end(), raw, "na", "no tool result has this context"))
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
