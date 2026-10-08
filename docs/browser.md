# The browser interface (bench2agent)

This is the project's earlier product: a local site for reading one
conference edition — search, topics, methods, the sentences a paper is
built on — with a chat whose every quote is machine-verified against the
paper. Since the 2026-10-02 re-centering on benchmarks it is the supporting
evidence surface: a benchmark's count opens onto these pages. Its design
principles are in [paper-view.md](paper-view.md). The browser and terminal share the `bench2agent` command and
`BENCH2AGENT_*` environment variables. The hosted service design is in
[database-and-hosting.md](database-and-hosting.md).

## Install

The corpus and search engine run on your machine and are served on loopback.
The agent sends questions and selected evidence to its model provider.
Requirements: git and Python 3.10+. The data bundle is a GitHub release
asset, fetched with the [gh CLI](https://cli.github.com) (`gh auth login`
once) or with `fetch-data --url <release asset>` without it.

**Linux / macOS** — two lines:

```bash
curl -fsSL https://raw.githubusercontent.com/hyunyoungnam/Bench2Agent/main/install.sh | bash
bench2agent fetch-data --release data-20260923      # site + search index, ~450 MB
```

**Windows** — install WSL once (PowerShell: `wsl --install`, then reboot),
open the Ubuntu terminal, and run the same lines. Everything below happens
inside WSL; the browser on Windows reaches it at the printed address (under
WSL the server binds to the VM's interfaces rather than its loopback, since
Windows' localhost relay cannot reach the latter; the VM's NAT keeps it off
the LAN).

The script installs into `~/.bench2agent` (an app directory — everything in it,
data included, stays inspectable), puts the `bench2agent` command on PATH via its
own venv, and fetches the search-engine binary and keys; the clone and the
data bundle both go through gh's credentials. (`BENCH2AGENT_RELEASE=data-20260923`
before the installer folds the fetch in; `BENCH2AGENT_BUNDLE=<file>` unpacks a bundle
you already have.) Then:

```bash
bench2agent serve
```

which starts everything on one port and prints its address:

```
  local:    http://127.0.0.1:8001
```

The server binds to loopback only. It is not reachable from other machines,
by design: a question asked on this page spawns a coding agent signed in on
*this* machine, so an address anyone on the network could open would be that
account handed out without a login. Each reader runs their own copy instead.

Ctrl+C stops everything. `bench2agent status` shows benchmark data and model
connections; `python -m bench2agent.core status` shows the browser processes. `bench2agent datasets --out DIR` writes three CSV inventories:
`benchmarks_used.csv` (abstract-named benchmarks and their mapped locations),
`datasets_released.csv` (paper-owned artifact links with evidence), and
`benchmarks_introduced.csv` (paper-claimed new benchmark candidates). The
running server serves them at `/datasets/<filename>` and reports coverage at
`/datasets/summary.json`; append `?gid=156` or use
`bench2agent datasets --gid 156 --out DIR` for one paper. These files contain
source links and evidence, not the external datasets' records. Answers are
written in English; Korean rendering is off by
default and `BENCH2AGENT_KO=1 bench2agent serve` turns it on.

The data bundle is produced by `bench2agent bundle` on a build machine and
published as a GitHub release; a new release reaches an install with one
`fetch-data --release <tag>`.

### VS Code

The [VS Code extension](../vscode-extension/README.md) starts this same local
service automatically and opens chat or paper exploration in an editor tab.
Open the repository in VS Code, run **Run Bench2Agent Extension** with F5, then
use **Bench2Agent: Ask About Benchmarks** from the Command Palette in the new window.
The processed data bundle is still required. The current CSV links are source
inventories; exporting actual evaluation records is tracked in
[the implementation plan](evaluation-data-downloads.md).

### Connect a coding agent (no API key)

`python -m bench2agent.core mcp` is the browser MCP server over stdio with read-only tools for search,
similarity, topics, citations, each paper's verified sentences, and the six
benchmark tools. (The repository's `.mcp.json` registers only the benchmark
tools, through `bench2agent mcp`; see [terminal.md](terminal.md).) The agent
brings its own model, so no API key is involved.

## Asking it something

`/` is a conversation. Your question spawns **your own** coding agent (Claude
Code, signed in on this machine — no API key), armed only with this project's
MCP tools over the held corpus. Every factual sentence it writes must carry an
anchor `⟦gid|quote⟧`, and the server checks each quote against the paper's own
text **before the browser renders it**: a green check means the sentence
provably exists in that paper, a red one means it does not and is shown as
such. The agent's prose can still be wrong; the quotes cannot be invented.

That check is measured, not asserted — `scripts/audit/verify_bench.py` samples
the corpus and reports both directions:

| | |
|---|---|
| the papers' own extracted sentences, accepted | 200 / 200 |
| the same sentences edited (a swapped word, a dropped middle, two papers spliced), rejected | 418 / 418 |

**Numbers are checked the other way round.** A quote can be matched against a
paper; a figure was never written by anyone, it was computed — so a figure
carries the tool call that produced it (`⟦gap_scan:healthcare|31 name it, 3
attack it⟧`), and the server **runs that tool again** and checks every number
in the claim against the result. Three outcomes, and the third is not a
failure: matched, not matched, or *not recomputable* — the last for tools whose
answers are not reproducible (search ranking), which are never marked verified.
This is possible only because the corpus does not move.

A conversation has an address (`#c<id>`), keeps the tool trail it was answered
with, and exports as markdown or JSON — questions, answers, and a table of
every quote with the verdict it was given, stamped with the corpus it was
answered against.

## How the site is organized

- **Landing** — one card per conference, opening its newest edition, plus
  *Since last year*: which topics, methods, and benchmarks take a
  significantly different share of the conference than in the previous
  edition.
- **Inside a conference** — search plus three category rows (topic / method /
  benchmark). Nothing is listed until the reader narrows: showing all 6,637
  papers is the problem, not the answer. Result cards carry the highlighted
  passage, the extracted terms (proposes / builds on / compared with / data),
  the corresponding author where the paper names one, and links out.
- **Three views of the set you picked** — one card each, one **row** each (a
  table of the extracted fields, which is how a set gets compared), or the
  subgroups the embeddings support. Each paper takes a mark — read / later /
  not mine — kept in your browser; the marks never reorder anything, they only
  record what you decided. The set leaves as **.bib** or **.csv**.
- **An earlier year is never a browsing category.** Last year's edition exists
  as a baseline: it powers *Since last year* and appears among a paper's
  nearest neighbors (stamped with its year), nowhere else.

## How "Since last year" picks its rows

The chart answers "what is rising?" without ranking by opinion. Shares are
per-edition (papers per 1,000), because editions differ ~2× in size and raw
counts would only restate that. A category is shown when it passes **both**
tests, or is an anchor:

| Test | Rule | Why |
|---|---|---|
| Real | two-proportion z ≥ 2.576 (99%) | the corpus sizes decide what is sampling noise, not a hand-picked cutoff |
| Material | share moved ≥ 2 per 1,000, **or** ≥ 1.5× | statistically real but tiny drifts are not worth a row |
| Anchor | top-2 by current share, any change | the chart must also say what the biggest things are, or "rose" has no context |

At most 5 risers and 5 fallers are shown, ordered by current share. A category
with ≤ 2 papers in one year is tagged **new** or **gone** — at that count,
presence is indistinguishable from noise. In the bar, the pale band is last
year's share and the dark band is this year's, the longer drawn underneath so
the tail stays visible — a dark tail grew, a pale tail shrank. The figures are
"was → is", each as that year's share of its conference.

The comparison is computed from the same abstract-level extraction for both
years — never from full text, which only exists for part of one year and
would make that year look artificially richer.

## Principles

- **Extractive, never generative.** The local model selects sentences; rules
  cut them (pure deletion, verified as an ordered subsequence); nothing on
  screen is model-written prose. A span that fails verification against the
  paper is dropped, not repaired.
- **Show differences, never importance.** Cluster, count, share, and change
  are measurable and shown; "promising" and "breakthrough" are not. With 83%
  of papers claiming novelty, ranking by it would rank phrasing.
- **Coverage is stated.** Topic tags reach about half the corpus; full text
  exists for ~72% of 2026; contact lines for 65% of full texts. Gaps are
  shown as gaps.
- **If a view needs a sentence to be understood, redesign the view.**

## Pipeline (summary)

Feed collection → abstract scrape → normalization (dedup: orals are listed
twice) → arXiv/PMLR full-text fetch and sectioning → structured extraction
with verbatim-span verification (local vLLM) → shared frozen taxonomy →
union embeddings and cross-venue neighbors → the site (`reports/`) plus a
Meilisearch index, packed by `bench2agent bundle` for installs. The pipeline needs a
GPU machine; an install only serves its output.

The full build documentation, data traps and measured quality numbers are
in the maintainers' notes, which are not part of this repository.
