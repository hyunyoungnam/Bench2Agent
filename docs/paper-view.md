# The paper-reading surface — principles

These sections were the core of the maintainers' build notes until 2026-10-02,
when the project re-centred on benchmarks. They
are moved here verbatim, not retired: the cards, the selected-set views, the
chat shell and the verifier are still in the code and still serve as the
evidence surface behind every benchmark count. **These principles bind any
change to those screens.**

Section references such as *Guardrails*, *Two extraction passes*, *Traps* and
*Problem 1* point to those notes, which are not part of this repository
(*Problem 1* is now titled *Domains and topics*; *Problem 4* is *Where to get it*).

---

## The product is processing, not access

Access is already solved. icml.cc lists every paper, arXiv serves the PDFs, and
nobody needs a fourth link list. **We do not ship access to text — we ship the
result of working that text over.**

This settles what the 26 GB of PDFs are for. Full text is an **input to
processing**, never a thing to serve. Do not build a PDF reader, a full-text
search box, or a "read it here" pane; the paper's own page is one click away and
always better. Full text exists to make our processed view carry more than a link
would.

It also settles a question an abstract cannot answer. **An abstract frequently
does not let a reader tell what a paper actually does** — which is a large part
of why a reader is stuck in front of 6,600 of them. A card that reprints abstract
prose has added nothing. The processed view must beat the abstract, or it has no
reason to exist.

**What "processing" is allowed to mean.** Selecting, decomposing, arranging,
counting, and placing — over the paper's own sentences. Not writing new ones.
Guardrail 2 is the hard edge here and is unchanged: every displayed string is
either extracted verbatim or computed from `data/processed/`. "Better than the
abstract" is earned by **structure** — the right sentence pulled out, the claim
split into proposes / builds on / compares against / data, the paper placed among
its neighbors — and never by paraphrase. **A fluent model-written summary is a
regression here, not an improvement**, however much better it reads.


---

## Problem 2 — Novelty

### Extract sentences. Do not write them.

The novelty of a paper is shown as **verbatim spans from that paper**. Never a
model-written summary in their place.

Why this is not a stylistic preference:

- An extracted sentence provably exists in the paper and can be checked in two
  seconds.
- It carries the authors' own words and hedges.
- There is nothing to hallucinate.

Structured fields (proposes / builds on / compares against / datasets) are a
**navigational index over the spans**, not a replacement for them. Every
structured claim must be traceable to a span.

### The card is one edited passage, not a form (2026-08-18)

The three spans are no longer three labeled rows. They run together as a single
passage in the order a reader needs, and **color carries the role** instead of a
label:

| Highlighter | The question it answers |
|---|---|
| pink | why the work was needed — what prior work could not do |
| yellow | what is new |
| sky blue | what it achieved, with the paper's own numbers |

Text stays near-black on a translucent wash, so contrast is ~14:1 under every
color. The extracted terms (proposes / builds on / compared with / data / tasks)
sit inside the passage block, not in a footnote under it — they are part of the
summary, not metadata about it.

**Elision now also cuts the announcement.** "We introduce a framework that…"
became "a framework that…". Without it the card reads as scraped text; with it,
as an edited sentence. Still pure deletion, still verified as an ordered
subsequence — `_ANNOUNCE` in `site.py` lists the verbs, and it never fires if
fewer than 30 characters would remain.

### Who to write to comes from the paper, or not at all

The feed has no email field — checked, it does not exist. The ICML template
prints `Correspondence to: Name <addr>` on page one, and `icml.contacts` parses
exactly that: **3,077 of 4,770 full texts (65%), 46% of the corpus**. Papers that
do not print the line show their first author's name with no address. Guessing
that the first or last author is the corresponding one would invent a fact the
paper states plainly whenever it states it at all.

These addresses are published in the papers themselves, but republishing them in
bulk is a different act from printing them once — worth a deliberate decision
before the page is made public rather than unlisted. **Decided 2026-09-03
(owner): published as-is with the public release** — they are the papers' own
printed facts and contacting authors is normal academic practice. Revisit on
complaint.

### The three difference spans (added 2026-08-13)

A novelty span alone states a *claim*: "we provide an improved analysis under
much milder conditions". Milder than what? The reader cannot tell, so they open
the paper — and the card has done nothing. Measured on the 2026 census: 31% of
spans open with "we propose/introduce", and median title-word overlap is 25%.

The schema therefore carries three more **verbatim, verified** single sentences:

| Field | The sentence that says |
|---|---|
| `limitation` | what existing work fails at |
| `key_change` | what this paper does differently — the mechanism, not the claim |
| `result_claim` | the outcome, with numbers where the paper gives them |

They go through the same `verify_spans()` check as `novelty_spans`; a near-miss
is a rewrite and a rewrite is not evidence. **This is not license to write
summaries** — the fields are cut from the paper and rearranged, and the reader
gets structure, not prose. See *The product is processing, not access*.

**That reversed once, then reversed back — and the second correction matters
more.** The full-text pass looked worse: 40.7% of its "verbatim" quotes failed
verification against 5.5% for abstracts. The obvious reading was that the model
invents more when given more text.

**It does not.** Measured on 80 spans pulled from camera-ready PDFs:

| | |
|---|---|
| passed the old whitespace-only check | 75.0% |
| passed once the line-break hyphen was repaired | +17.5% |
| passed once punctuation was ignored | +2.5% |
| **actually absent from the source** | **0.0%** |

The verifier was not strict, it was **brittle**. It rejected the paper's own
sentences because of how the typesetter wrapped them, and that rejection was
being read as a hallucination rate. `verify_spans` now normalizes line-break
hyphens and punctuation on both sides. It is deliberately NOT relaxed to
subsequence matching: a subsequence of a 24,000-character document is nearly free
to satisfy and would stop being evidence.

**Full text is the better source for these three fields, not the worse one.**
Where the abstract says "existing methods are limited", the paper says "prior
work completes around 20 classes"; where the abstract says "we evaluate on
standard benchmarks", the paper says "~50% of PaSCo on SemanticKITTI". Papers
whose abstract states no limitation at all frequently state one in the intro.

So the passes are merged **field by field, not record by record**: the abstract
leads, full text fills only the fields it leaves empty. That union reaches 94% /
93% / 88% for limitation / key_change / result_claim, and 2,898 papers take
something from full text. `_PDF_HYPHEN` in `site.py` repairs the artifact at
display time, distinguishing a split word (`condition- ing` → conditioning) from
a real compound (`token- level` → token-level).

### Known and accepted: claims do not discriminate

Measured on this corpus:

- **82.9%** of abstracts contain an explicit novelty claim
  ("we propose", "novel", "first", "outperform")
- **82.0%** are classified `new-method`
- **11,515** distinct proposed names, of which **98% appear exactly once**

So "extract the novelty claim" returns ~5,400 structurally identical claims:
*we propose X, it is new, it beats Y*. Extraction is easy; discrimination is not.

**Decision (2026-07-31): we do not attempt relational novelty for now** —
novelty measured against what a reader already knows, or against a paper's
neighbors. The product shows what each paper states about itself. Revisit only
on explicit request.

Consequence to respect: **do not rank papers by novelty.** With 83% claiming it,
any ranking would be an artifact of phrasing. Show what a paper says is new; let
the reader judge.


---

## Problem 3 — Showing the set

Filtering 6,637 down to 40 is not the finish line. **Forty rows in a list is
still forty papers of reading**, and a reader who cannot tell which to open first
has been handed the original problem at 1/166 scale.

**The unit of the view is the selected set.** Once a reader has picked a field —
by tag, by subarea, by search, or by seed paper — the view's job is to make that
set legible at a glance: where its papers clump and where one sits alone, which
methods and datasets recur across it, which subareas it straddles, which are
orals. That is a visualization problem, and it is the part of this product that
is least built.

**This is not the corpus atlas that was rejected on 2026-07-31.** The distinction
is the scope, and it is the whole difference:

- The atlas drew all 6,637 papers and answered *"what is this conference like"* —
  a question no attendee asked, producing a picture nobody could act on.
- A field view draws **the 40 the reader chose** and answers *"which of mine do I
  open first"* — the decision they are actually stuck on.

If you find yourself drawing the whole corpus again, you have slipped back.

**Guardrail 1 still binds, and this is exactly where it gets tested.** "Which do
I read first" must be answered with *structure*, never with a score: cluster,
spread, recurrence, overlap, and relation are measurable and may be shown. A
computed "importance", "impact", or "read this first" ranking is not, and must
not be built — see also *do not rank papers by novelty* above. The reader ranks;
we make ranking possible.

Material already available for this: `landscape.json` coordinates (6,592 of
6,637 — the 45 papers without abstracts have none, and must be shown as
uncounted, not dropped silently), `neighbors.json`, the extracted method/dataset
/task vocabulary, topic tags, and the oral/spotlight flags.

**Built 2026-09-09 — the selected set now has four views** (`st.view` in
site.py), and each carries a rule that keeps it on the right side of
guardrail 1:

| View | What it is | The rule it keeps |
|---|---|---|
| papers | one card each, as before | — |
| **table** | one ROW each: proposes / builds on / data / tasks side by side, which is how a set is compared | sorting is alphabetical and puts named values before empty ones; there is no sort by merit, and coverage per column is printed under it |
| **map** | the selection placed by ITS OWN vectors — a 2-component projection computed in the browser over the picked papers, never corpus coordinates | frame is the 1st–99th percentile (5th–95th built visible fake clusters along the edges); what is pinned, what has no vector, and the 600-dot cap are all stated. Dragging a box keeps what is inside it, as a removable chip like every other pick |
| subgroups | the existing embedding split | unchanged — `groupsFor` still refuses to invent a split |

**The reading queue rides on top of all four** (ASReview's rule, kept intact:
the model ranks nothing, the reader decides). Each paper takes read / later /
not mine in `localStorage`; the marks never reorder anything, they show
progress through the set, and hiding the excluded ones leaves the set COUNT
unchanged — the list narrows, the number still says how big the field is.

**The set can leave**: `.bib` and `.csv` from the rail, built server-side
(`bellwether/export.py`) because BibTeX needs the full author list, which the
page payload does not carry. A conversation exports the same way, as markdown
or a JSON bundle with every quote, its verdict, and the corpus it was answered
against.


---

## Interface principles

- **The conversation is the front door (2026-09-03, owner — supersedes the
  briefs-landing earlier the same day).** `/` serves the chat shell
  (`reports/chat.html`, shown name: **Frontier**, provisional): the reader's
  question spawns their own logged-in coding agent (Claude CLI headless, no
  API key) armed with only the bellwether MCP tools; every anchor `⟦gid|quote⟧` is
  verified server-side BEFORE display (`bellwether/verify.py`), tool calls stream
  live as the trail, conversations persist in `data/chats/`. The built corpus
  view moved to `/browse` and remains the evidence surface — cite chips open
  `/browse#p<gid>`, that paper's card selected and unfolded. Guardrails
  transfer: the agent's prose may be wrong and is never dressed as the
  paper's words; a green check means the quote provably exists in the paper.
- **[superseded 2026-09-03B: briefs were absorbed into PUBLISHED CONVERSATIONS
  — a chat's ★ publish flag; /browse landing is venue cards + field index only;
  the research-brief skill and verify_brief.py were removed]**
  The landing is a research workspace, not an analytics page (2026-09-03,
  owner decision — supersedes the "analysis first" landing of 2026-08-24).
  The landing shows: search, the RESEARCH BRIEFS list, venue cards, and the
  quiet all-fields index. The digest/since-last-year/struggles charts left the
  landing — do not restore them there; their computations remain build-time
  material destined for agent tools. A brief is written by the reader's own
  coding agent (Claude Code/Codex via the bellwether MCP server, `.claude/skills/
  research-brief`), saved to `reports/briefs/`, and verified by
  `scripts/verify_brief.py`: prose is the agent's, every cite carries a
  verbatim quote checked against the paper, unverified quotes are marked on
  the page, never hidden. Cite chips open the paper's card on the ALL screen.
  Briefs cite by **gid**; the page's `P` is row-indexed with the gid in `.i`
  — `rowOfGid()` is the only legal bridge (the gid trap, again).

- **No self-describing blurbs (2026-09-03, owner).** The interface never
  explains its own mechanism or virtues in copy — no "answers with your
  agent's account", no "every quote verified before display", no legend
  sentences decoding ✓/✗. Affordances teach by use (a tooltip on the mark, an
  example chip); a caption may state a unit or coverage figure, nothing more.
  If a screen seems to need such a sentence, that is the redesign signal
  below, not a licence to write it.
- **If a view needs a sentence to be understood, redesign the view.** This is the
  stated goal of the interface: intuitively readable without auxiliary
  explanation. Captions may state a unit or a coverage figure; they must never
  carry reading instructions. (The "Since last year" chart went slopegraph →
  paired bars for exactly this reason: a slopegraph needs to be explained, two
  bars of different length do not.)
- **The main page is every conference, analysis only (2026-08-24).** Headline
  tiles (one rule-picked fact per angle) lead; the sections follow; the venue
  cards sit at the BOTTOM as scope filters ("one conference at a time"), and
  papers are never browsed on the main page itself. Scope -1 ("#all") is a
  results screen over the union — every digest door lands there, with the
  venue-year stamp telling papers apart; a venue card scopes the same screen
  to one conference. The ladder from analysis to papers: a digest row expands
  in place (lanes + the papers CARRYING the shift, orals first) → clicking an
  evidence title opens the ALL screen scrolled to that paper's card → "open
  the N papers" opens the whole set.
- **Analysis first, selection second (2026-08-24).** The landing leads with
  what MOVED, and every printed fact is a door that applies itself as a
  selection. Below the since-last-year chart: WHERE THE MIX SHIFTED (inside a
  field, which building blocks / tasks / co-domains took a different share of
  the set — "image generation: diffusion 48→25%, autoregressive new"), WHAT
  THE FIELD FIGHTS (failures named in limitation sentences, share per 1,000),
  NEW THIS YEAR (benchmarks no earlier-edition paper used), and a quiet
  "browse all fields" index for the reader whose field did not move. Rows
  expand in place to lanes plus an "open the N papers" button — preview before
  commitment. All of it is precomputed at build time in `build_digest`-style
  code inside `site.py`, so first paint scans nothing.
- **The digest bar is the product's standing rule, disclosed — not FDR.**
  Measured: the mix family is ~120 tests whose strongest true shifts sit at
  z 2.6–3.2; Benjamini-Hochberg at q=.05 with that m zeroes the digest while
  the top of the list is unmistakably real. So rows pass |z| ≥ 2.576 +
  materiality, the caption states the family size and that at most about one
  shown row could be chance. Appearing is its own evidence: 0 → 8+ papers
  (GRPO, RLVR) earns a row with no test at all, as does vanishing.
- **Limitation text is register, not only content — guard it.** "rely",
  "significant", "typically" shift across editions with huge z while naming no
  failure; a discourse stoplist plus a df ceiling (a term in >5% of either
  edition is the genre's phrasing) and a topic-word echo guard (a term that is
  a field's own name tracks the field, not a failure) keep the fights section
  about failures. At topic granularity the failure axis stayed register noise
  even after the guards — the mix section therefore scans builds-on / tasks /
  co-domains only, and the failure axis lives at corpus level where the sample
  carries it. The abstract-pass limitation index (`lims0`) is a build-time
  instrument only and is not shipped.
- **The title is a title again (2026-08-24).** The slot grammar ("What's new
  in [topic] for [field]…") tested badly with its first reader and is gone;
  the current selection still reads as removable chips under the title.
- **The failure glossary is OURS, and says so (2026-08-25).** Struggles rows
  define their term on hover with a curated one-line gloss (FIGHT_GLOSS in
  site.py) — interface copy in the same class as a caption stating a unit, not
  paper data — visually separated and labeled "our gloss", with the papers'
  own sentences beneath as evidence. Guardrail 2 still governs everything
  attributed to a paper; the gloss never is.
- **A venue's cold screen leads with what the venue put forward (2026-08-26).**
  The corpus empty state lists the venue-declared highlight set (spotlights /
  ICLR orals) captioned "the venue's own selection, not ours", with coverage.
  Highlight papers carry a DEEP pass — `extract_facts --source deep` pulls
  mechanism / numbers / ablation / own_limits as verbatim verified sentences
  into `facts_deep_<key>.jsonl`. Display (revised 2026-08-27): full text
  serves the PASSAGE, never a side ledger — the mechanism sentences join the
  yellow wash inside the passage (deduped by token overlap); numbers /
  ablation / own_limits stay on disk unused for now. Every body sentence
  passes the STANDALONE gate (site.py `standalone()`): no document deixis
  ("Lines 11-14", "Figure 2", "as described below"), notation below a small
  density bar — an equation on a card is the reading the reader came to
  skip. A failing sentence leaves the field to the abstract pass. Guardrail
  5 still binds: nothing counts, sorts or filters on deep fields.
  Cards fold top/middle/bottom (2026-08-27): title-venue-year / passage /
  terms; the list default hides only the middle, a click unfolds it — the
  terms alone answer "which problem, what name, on what, which data".
- **Citations serve two views, neither a shared-bibliography list (decided
  2026-08-28).** A pairwise shared bibliography restates similarity and was
  removed. Instead: the compare view states the RELATION — "the left paper
  cites the right one" / each cites the other / neither — one line above the
  cards. And the set sidebar shows STANDS ON: the papers most of the
  selection cites, counted within the selection (>=3 citers, top 5) — the
  field's load-bearing ancestors; every row is a door narrowing the set to
  the papers citing that ancestor (chip x to leave). Data: citations.json
  edges ride the spans parts per paper (slot 6). A retained governor-cite
  name in a passage still links: in-corpus -> compare ("the paper it
  cites"), else arXiv/DOI/S2 via the paper's own or Semantic Scholar's
  parsed references — never a guess.
- **Cited-in sentences, verbatim and unclassified (2026-09-10).** scite's
  question without scite's verdicts: an unfolded card shows what later corpus
  papers say AT the citation — the citing paper's own sentence, located by
  the reference head's natbib tag (icml.cite_contexts), never paraphrased,
  never labelled supporting/disputing. They ride the spans parts as slot 8
  (one sentence per citer on the page; the two-sentence record stays in
  cite_contexts.json and surfaces in get_citations as "context"). Trap: the
  body searched must be the SAME fulltext file the references came from —
  the PDF twin of an HTML row renders citations differently and silently
  drops coverage (measured 70% -> 57%). Numeric reference styles yield
  nothing rather than guesses (23% of edges). The legend doubles as the role
  toggle (st.role): click a colour to read every card by one question —
  display-level only, guardrail 5 still binds, the set never changes.
  External ids (icml.ids -> ids.json) map by exact arXiv id only;
  icml-2025/PMLR stays unmapped rather than title-guessed; exports carry
  eprint/doi/openalex/s2 when the map exists and omit them when it doesn't.
- **Picking a similar paper opens a side-by-side compare (2026-08-25).** The
  Similar panel's neighbor click no longer scrolls-or-walks: it opens a
  two-card split view — the read paper left, the picked one right, a "both
  papers" strip naming their shared methods/data/tasks above. The sidebars
  leave (the question at that moment is "what differs between these two",
  not "what is in the set"); selection state is untouched and × restores the
  list exactly. No signal may say which side is better — left is only "what
  you were reading". Similar on either card exits to that paper's panel, so
  the walk continues.
- **"Who fights my problem" is an entry path (2026-08-20).** A second input
  searches ONLY the extracted limitation sentences (89% of papers state one; a
  salient-term index of unigrams + df>=5 bigrams ships in the payload). The
  chosen string matches as a substring of the interned terms, so "hallucinat"
  is hallucination/-s/-ed at once; suggestions show the union count first and
  the narrower inflections under it. Matches mark the phrase inside the card's
  pink limitation span. This axis is orthogonal to every taxonomy — it groups
  papers by the failure they attack, in their own words.
- **The landing is conference-first (2026-08-19).** One card per venue, opening
  the newest year. An earlier year is never a browsing category — nobody goes
  back to browse 2025 — it exists as the baseline: it feeds the "Since last
  year" chart and appears in a paper's neighbors, stamped with its year, and
  nowhere else.
- **"Since last year" shows what a rule selects, not a top-N.** A row is
  colored and labeled only when its share change clears a two-proportion
  z-test at |z| ≥ 2.576 (99%) — the corpus sizes decide what is noise, not a
  hand-picked cutoff. The two largest current shares are kept as gray anchors.
  Everything else is not drawn: the gray context mass was tried and is what
  made the chart unreadable.
- **Search first, then show the set, then the paper.** Those are the three
  questions in order. A picture of the whole corpus is not a step in that path
  (see *Problem 3*); a picture of the reader's 40 papers is the missing one.
- **Every view must help decide what to open.** That is the test to apply before
  adding anything. A view that only proves the papers exist has failed it, no
  matter how much it displays.
- **Results must be pre-decomposed** — a result row shows *proposes → builds on →
  data*, not just a title. Scanning 50 results should take a minute.
- **Minimize prose, but never at the cost of a caveat.** Coverage and method
  notes stay.
- **One core page + on-demand parts (2026-08-24).** Still no build step and no
  Node, but no longer one file: index.html inlines only what first paint and
  every count need (5.3 MB raw, 1.4 MB gz); card sentences (per corpus), the
  search/limitation indexes and the vectors live in `data/*.json`, fetched on
  first touch plus an idle prefetch. Cards render instantly with a "loading the
  paper's own sentences…" line that fills on arrival. Consequence: file://
  preview no longer works — use the `python3 -m http.server` that already backs
  the tunnel. Sized for six corpora; a single file was 21.8 MB raw at two.
- Charts follow the project dataviz standard; **load the `dataviz` skill before
  touching one.** A scatter is an all-pairs form and caps at three categorical
  hues — research areas are never eight colors; use emphasis instead.
- **A figure is verified by recomputation, a quote by matching** (added
  2026-09-09). An answer's numbers were never checked: the anchor protocol
  covered sentences only, and a mistyped z or a wrong count passed unseen —
  the closest published measurement puts numerical fabrication at 38.2% of RAG
  failures (SIGIR '26). Figures now carry `⟦tool:argument|the figures⟧`; the
  server runs that tool again on this machine and every number in the claim
  must appear in the result. Three outcomes and the third is not a failure:
  ok / no / **na** ("could not recompute" — a tool outside the list, or an
  argument that no longer resolves). Rules that matter:
  - **only deterministic tools** are recomputable (`figures.RECOMPUTABLE`):
    field_trend, gap_scan, topic_papers, field_cards, get_citations,
    get_paper. `search_papers` is excluded — its ranking and estimated total
    come from the engine, and a ✓ that is not reproducible is worse than none.
  - **the claim's own precision is the tolerance**: "4.4" accepts 4.43, "1.15"
    does not accept 1.2. (FinGround uses a domain constant, ±0.5%; the claim's
    printed precision needs no constant.)
  - **ids are not figures.** A gid would make almost any count match, so paper
    lists never enter the pool — but a skipped list's LENGTH does, because "28
    papers cite this" is exactly that count. Numeric dict KEYS count too
    (`by_year` is `{2025: 13}`), and they are ints in-process where the agent
    saw strings.
  - a single small integer is weak evidence (a "3" is in almost any result), so
    an anchor passes only when EVERY number in it matches.
  This is only possible because the corpus does not move — the provenance
  literature verifies against a recorded trace precisely because re-calling a
  web tool would answer differently (a survey of prior work on this is in the maintainers' notes).
- **The check does not wait for the agent to declare it** (added 2026-09-09,
  after measuring). Asking the agent to anchor its figures reached **38%
  coverage**: it anchored some numbers and printed bare copies of the same ones
  a sentence later. Verification that depends on the writer's cooperation is
  not verification. So the server now scans EVERY number an answer prints and
  checks it against the tools that turn actually called (the trail is stored
  with the turn for exactly this). Measured on the bare numbers of three real
  answers: 84% appeared verbatim in a tool result, 7% were a ratio or
  difference of two of them (`derived_set` accepts that — an agent computing a
  share is not inventing one), and the last 9% were a thousands-separator bug
  (`3,324` read as `324`), the project's own significance threshold, and a gid.
  None was invented. After the fixes a fresh answer checks 30 of 30.
  Consequences to keep: years and the answer's own cited gids are not figures;
  a tool that applies a threshold must **state that threshold in its output**
  (`field_trend.rule`) so a sentence quoting the bar verifies like any figure;
  an explicit `⟦tool:arg|…⟧` anchor still binds a figure to ONE call and is
  the stronger form; and a number the server cannot place is marked as the
  server's own notice — a dotted underline, not a red ✗, because "not in this
  turn's tools" is a fact about our reach, not an accusation about the claim.
- **Run the audit suite before calling an interface change done** (added
  2026-09-08): `scripts/audit/run.sh` — routes + MCP tools + verifier
  (`api_audit.py`), the verifier benchmark (`verify_bench.py`: the papers' own
  sentences must verify, the same sentences edited must not — 200/200 and
  418/418 on 2026-09-09, and it FAILS the run if the matcher is relaxed), then
  three same-origin harnesses that drive the real pages
  in an iframe and assert on what a reader would see (chat shell, `/browse`,
  and two live agent runs including a stopped one). Each harness holds the
  load event open with `/slow?s=N`, so `firefox --screenshot` captures the
  finished report. Module state is reachable only through `window.BW` —
  `let` bindings are not window properties.
- **Look at what you built** before calling it done:
  ```bash
  firefox --headless --window-size=1400,1200 \
    --screenshot ~/bellwether/reports/.preview/shot.png \
    file:///home/hyunyoungnam/bellwether/reports/<file>.html
  ```
