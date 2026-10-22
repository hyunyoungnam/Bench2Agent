# Bench2Agent

[![Code release](https://img.shields.io/github/v/release/hyunyoungnam/Bench2Agent?filter=v*&label=release)](https://github.com/hyunyoungnam/Bench2Agent/releases)
[![PyPI](https://img.shields.io/pypi/v/bench2agent)](https://pypi.org/project/bench2agent/)
[![Data release](https://img.shields.io/badge/data-2026--10--21-blue)](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/data-20261021)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/hyunyoungnam/Bench2Agent/blob/main/LICENSE)

Ask which benchmarks researchers evaluate on, how usage changes across years,
and which new benchmarks other researchers adopt. Bench2Agent connects your
signed-in Claude Code or Codex to tested benchmark tools and checks answers
against the tool results and original paper evidence.

Use it in a terminal or in a local browser workspace. Both interfaces share
the same AI CLI login, conversations, research data and answer checks; the
browser runs on your computer, without a hosted backend.

**Usage frequency describes adoption, not benchmark quality.**

[Install](#get-started) · [Browser workspace](#local-browser-workspace) · [Data pipeline](#from-papers-to-evidence) · [User guide](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/terminal.md) · [Releases](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/releases.md)

## Get started

You need **Python 3.10+** and **Claude Code or Codex installed and signed in**
on your computer. Bench2Agent and the official AI CLI are separate installations.
If needed, sign in using `claude auth login` or `codex login` first.

Install into an active Python environment; a virtual environment is recommended:

```bash
python -m pip install --upgrade bench2agent
```

On macOS/Linux, [uv](https://docs.astral.sh/uv/getting-started/installation/)
provides an alternative isolated installation:

```bash
uv tool install --upgrade bench2agent
```

Choose **one** installation method, then choose an interface:

| Interface | Start | Use it for |
|---|---|---|
| Terminal | `bench2agent` | Ask questions and follow-ups, resume conversations, inspect sources with `/sources` |
| Local browser | `bench2agent --web` | Read answers beside benchmark cards, comparison tables, original evidence and data coverage |

`bench2agent --web` opens the workspace in your browser; `--no-open` prints its
address instead and `--port N` chooses a port. Keep that terminal open while
you use it; Ctrl+C stops the workspace.

On the first interactive launch,
Bench2Agent automatically downloads the research-data bundle (about 45 MB),
verifies its checksum and installs it as a data revision. Subsequent
launches reuse the data. No GPU, database server, Bench2Agent account or API key
is needed for the CLI-login route. Model usage follows your AI account's limits.

If both AI CLIs are signed in, choose one at the prompt. Your choice is saved.
To choose explicitly:

```bash
bench2agent --connector claude
# Or:
bench2agent --connector codex
```

Ask a question, then a follow-up. `/sources` shows evidence, `/chats` lists
saved conversations, `/resume ID` continues one, `/new` starts fresh, and
Ctrl+C cancels an unfinished answer. Saved conversations keep their data
revision after updates and can be continued in either interface. Linux/WSL
is tested; macOS follows the same POSIX path. Native Windows CLI cancellation
has not yet been validated.

API access is an explicit alternative: `bench2agent --connector api` prompts
for the provider and a hidden, session-only API key. API billing is separate
from a Claude or ChatGPT subscription.

## Local browser workspace

Ask **“Which benchmarks do recent robotics papers evaluate on most?”** and open
the evidence panel to inspect the answer's counts and scope:

![Local browser workspace answering the robotics question, with evaluation-use counts and percentages in the Compare panel](https://raw.githubusercontent.com/hyunyoungnam/Bench2Agent/main/docs/images/robotics-web.png)

*Actual local browser session using Claude Code and revision
`1e1fa171722da693`; answer wording varies. The Compare panel is built from the
recorded tool results, independently of the model's prose.*

Use **Benchmarks** for resource links and introducing papers, **Compare** for
counts and percentages, **Evidence** for original paper sentences, and **Data
coverage** for the conference editions, filters and analyzed-paper counts
behind the answer. A percentage describes use among the analyzed papers,
not every paper published at a conference.

The terminal uses the same tools and checks; `/sources` opens the evidence.
Quotes are matched to their source paper, and numbers are checked against the
same turn's tool results. A dotted underline in the browser, or an
`[unverified figure]` in the terminal, means the checker could not connect a
number to the evidence; it does not necessarily mean the number is wrong. A
number that differs from a tool value in the same context is marked as such
(`[differs from tool result]`, red in the browser); hover or focus a marked
number in the browser to see why.

Verified resource links and **links printed in a paper** are shown separately.
The latter do not establish a benchmark connection. Resource observations say
what was checked and when; a repository link or file listing does not guarantee
that the benchmark data can be downloaded or that its records were validated.

## From papers to evidence

Conference sources provide the paper lists, titles, authors and source IDs.
The pipeline resolves papers to arXiv, archives the available HTML and extracts
structured evidence from it. Benchmark-use extraction is rule-based; the
answering model chooses tools and explains their results rather than counting
papers itself.

| Information | What is extracted or retained |
|---|---|
| Paper text and structure | Sections, table and figure captions, table cells, references and inline citation links |
| Benchmark identity | Matched benchmark names and spellings, stable catalog IDs and source-backed aliases; ambiguous names remain separate |
| Evaluation and training use | Statements that the paper evaluates or trains on a benchmark, stored with the role and original evidence text and location |
| Introduction claims | Sentences proposing a new benchmark or dataset; only reviewed claims enter the introduction catalog, with the review method recorded |
| Resource links | URLs in release statements and front-matter lines such as “Code — URL”; individually reviewed benchmark locations remain distinct from unconfirmed paper links |
| Source provenance | Archived HTML SHA-256 and arXiv version when known; evidence keeps its paper and location, and reviewed paper links also retain their source element and review record |

References or name mentions alone do not establish use. Text assembled from
table cells is labeled as assembled evidence rather than presented as a
verbatim sentence. Introduction and location reviews performed by Claude Code
are recorded as **AI reviews**, not human reviews.

Dates describe different events: fetch and re-check times belong to the
processing history; an **OAI datestamp** signals a metadata-record update, not
a confirmed change to the paper's text. The arXiv version and HTML hash identify
the source when known. Link observations have their own check dates, and each
published database records its build time. Unknown dates stay unknown.

## How the data is stored

The builder writes directly to a **local SQLite database**, with normalized
tables linked by paper and benchmark IDs:

| Tables | Stored relationships |
|---|---|
| `editions`, `observations`, `observation_authors` | Conference and year, paper IDs, authors and coverage; one observation per paper and conference edition |
| `benchmarks`, `benchmark_aliases`, `claims` | Benchmark identity, aliases and reviewed introduction evidence; resource records and paper links are retained as structured metadata |
| `uses`, `use_roles`, `use_surfaces`, `evidence` | Which paper uses which benchmark, evaluation versus training, observed spellings and supporting passages |
| `observation_labels` | Subject labels with their source, method and version, preserving conference tracks separately from research fields |
| `unattributed`, `unattributed_candidates` | Mentions that cannot be assigned confidently, with candidate benchmark IDs and reasons |

Raw HTML archives and intermediate extraction files stay in the build
environment. A separate working database tracks processing stages, source
versions, retries and review queues. The downloadable research database
contains the catalog, observations, counts and evidence; it does not contain
the processing queue, private conversations or the complete HTML archive.
External benchmarks' actual questions, labels and images are separate downloads.

Both interfaces query this database through the same `BenchmarkStore` and six
MCP tools: coverage, usage, trends, introductions, adoption and evidence.
**Usage** counts paper–edition observations; **adoption** counts unique papers
and separates other authors, introducing authors and unknown relationships.

Each installed database is an **immutable data revision** with a manifest,
content hash and file checksums. New data installs alongside existing revisions;
saved conversations keep the revision used for their first answer, so follow-up
questions and answer checks remain on the same data. No database server is
required. [Database design](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/database-and-hosting.md) · [Counting and evidence rules](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/benchmark-chat.md)

## Use the tools inside Claude Code or Codex

After installation, these shortcuts download missing data and open the official
client with Bench2Agent's MCP tools:

```bash
bench2agent claude
# Or:
bench2agent codex
```

Ask the client to **use Bench2Agent**. In this route the client writes the
answer; Bench2Agent's final-answer checks run in the `bench2agent` conversation
interface. [MCP setup and scripted queries](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/terminal.md).

## Data coverage

The **2026-10-21 data release** (revision `1e1fa171722da693`, used in the
screenshot) contains **61 editions of 11 conferences**, **88,172 analyzed
paper-edition observations** and **9,334 benchmark/dataset entries**, including
8,770 entries with reviewed introduction claims and **315 benchmarks with
individually reviewed resource links (336 links)**: an AI review against the
introducing paper's own release line and the text at the link. It also adds
**70 unique links printed in 65 papers**, attached to 74 benchmark entries;
these paper links remain separate from verified resource links. Paper and
benchmark coverage is the same as in the 2026-10-19 release.

| Venue | Included editions |
|---|---|
| AAAI | 2020 · 2021 · 2022 · 2023 · 2024 · 2025 · 2026 |
| ACL | 2020 · 2021 · 2022 · 2023 · 2024 · 2025 · 2026 |
| CORL | 2020 · 2021 · 2022 · 2023 · 2024 · 2025 |
| CVPR | 2021 · 2022 · 2023 · 2024 · 2025 · 2026 |
| ECCV | 2024 · 2026 |
| EMNLP | 2020 · 2021 · 2022 · 2023 · 2024 · 2025 |
| ICCV | 2021 · 2023 · 2025 |
| ICLR | 2020 · 2021 · 2022 · 2023 · 2024 · 2025 · 2026 |
| ICML | 2020 · 2021 · 2022 · 2023 · 2024 · 2025 · 2026 |
| NAACL | 2021 · 2022 · 2024 · 2025 |
| NEURIPS | 2020 · 2021 · 2022 · 2023 · 2024 · 2025 |

Counts come from explicitly stated evaluation or training use in parsed arXiv
HTML, with the analyzed editions and denominator reported for each query.
Missing or unparsed papers remain unknown. Field filters match labels that
name a research field, each with its origin (the primary area the authors
declared at submission, the topic an AAAI track states, or a derived topic);
proceedings volume ids are kept but are not fields, and many editions carry
no field labels at all, so a field filter excludes those papers. New
introduction claims in the expanded editions are not yet reviewed, so
introduction discovery is less complete than usage coverage. “New” means a
reviewed claim in this corpus, not the first public release worldwide.

The 2026-10-21 bundle (data format v2) installs with Bench2Agent 0.5.0 or later
(0.5.0 serves it without the paper links or percentages); the 2026-10-13 bundle
(format v1) still installs from its explicit URL through a one-time conversion.
[Data rules](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/benchmark-chat.md) · [Database design](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/database-and-hosting.md)

## Updates

Version **0.6.1** is distributed on [PyPI](https://pypi.org/project/bench2agent/0.6.1/)
and [GitHub](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/v0.6.1). It uses the same
2026-10-21 data as 0.6.0, so no new data download is needed when upgrading from 0.6.0.
Code and research data update separately:

1. Update the code with your installation method (rerun its install command).
2. Run `bench2agent data install` to download and activate the new data.
   Installed data is never replaced automatically.
3. Start a new conversation (`/new`) to use it. Saved conversations keep the
   revision they started on.
4. If you use the tools inside Claude Code or Codex, restart that client so
   its Bench2Agent server reads the new revision.

Version 0.4.0 cannot install the new bundle and keeps working with the
2026-10-13 data. [Migration and data management](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/terminal.md).

The **0.6.0 local browser workspace** implements `bench2agent --web` with the
same conversation service as the terminal. It runs a loopback API and bundled
browser assets on your computer; a paid backend or hosted login is not required.
The two interfaces share saved conversations and data revisions. Browser mode
needs no `server` extra. [Browser guide](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/browser-interface.md).

The existing optional web/HTTP MCP modules are development prototypes. The
earlier hosted visitor prototype is deferred; hosted sign-in, strong visitor
isolation and public operation remain unvalidated; they are separate from the
local terminal, browser and stdio MCP interfaces.
[Account connections](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/account-connections.md).

Code is licensed under [Apache-2.0](https://github.com/hyunyoungnam/Bench2Agent/blob/main/LICENSE).
