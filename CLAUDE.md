# Bench2Agent: product direction and verified state

Updated 2026-10-22 after publication of 0.6.1 (data-20261021 unchanged). Read with
[AGENTS.md](AGENTS.md) for working rules and checks. Current state, follow-up
work and history are separated below; dated task logs and old instruction
backups do not override this document. Public references:
[README](README.md), [browser workspace](docs/browser-interface.md),
[data design](docs/database-and-hosting.md),
[evidence rules](docs/benchmark-chat.md) and [releases](docs/releases.md).

## Product direction

Bench2Agent helps researchers discover and choose evaluation benchmarks:
what papers evaluate on, how use changes across conference editions, what
benchmarks are introduced, who adopts them, where resources are available
and what evidence supports an answer. Benchmarks are the discovery unit;
papers provide evidence. Frequency describes adoption, not quality.

The model chooses tools and explains their outputs; code retrieves, counts
and verifies. Do not add quality rankings, institution rankings, autonomous
research-idea recommendations or a separate agent for every paper.

**Two interfaces, one local application.** Terminal implementation comes
first; the browser has its own visual and interaction design over the same
conversation engine. Both use the local owner's official, signed-in Claude
Code or Codex CLI, installed SQLite data, six MCP tools and verifier. An API
key is an explicit terminal alternative, with separate provider billing.

- `bench2agent` starts the terminal conversation.
- `bench2agent --web` starts the released local browser workspace and opens
  the default browser. `--no-open` prints the launch address; `--port N`
  selects a preferred port. Keep the process running; Ctrl+C stops it.
- The browser needs no paid backend or separate `server` dependency. It serves
  bundled assets and a small loopback API on the user's machine, with Host,
  Origin and launch-token protection. Provider credentials stay with the CLI.
- There is no Bench2Agent account, sign-up or invite flow. The official AI
  CLI is installed and signed in separately on each machine. Local histories
  belong to the installation, not to a provider-account identity.
- A Mac can run the local application; a dedicated Mac server and public
  multi-user hosting are optional future deployments, not browser requirements.
  The earlier hosted visitor prototype remains deferred.

The browser keeps conversation primary, with benchmark cards, comparison
tables and on-demand evidence. Cards and tables come from recorded tool
results, not numbers scraped from model prose. Show scope, missing coverage
and link status clearly. Keep dataset identifiers in data details; the
sidebar shows application version. Charts and rankings are not the default
experience. Reuse histories, follow-ups, revision pinning, verification,
cancellation and conditional saves across both interfaces.

## Current released state

**Bench2Agent 0.6.1 was published on 2026-10-22** on
[PyPI](https://pypi.org/project/bench2agent/0.6.1/) and
[GitHub](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/v0.6.1)
(annotated tag `v0.6.1` at `de08604`). It is a code-only update: stricter,
clearer number checks on the same data. 0.6.0 (2026-10-21) introduced the
browser workspace. Its [data-20261021 release](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/data-20261021)
remains the default data. The data tag (lightweight) and `v0.6.0`
(annotated) point to `60873ad`. Later main commits do not move tags.

| Area | Verified state |
|---|---|
| Terminal and local browser | Released in the base standard-library package; same `Session.ask`, CLI connectors and conversation files |
| Conversation lifecycle | Follow-ups and cross-interface resume preserve connector/revision; stale writes are refused; accepted cancellation saves nothing; browser reload reattaches to a job |
| Evidence panel | Benchmarks, Compare, Evidence and Data coverage are built from the answer's own tool log; resource links and paper-provided links are separate |
| Six read-only stdio MCP tools | Coverage, usage, trends, introductions, adoption and evidence; one counting implementation in `BenchmarkStore` |
| SQLite data | Direct database builder, schema 2, manifest format v2, content-based revision IDs; legacy v1 revisions/bundles remain supported |
| Answer checks | Quotes matched to their source paper. Each number is confirmed, contradicted (same context, another value) or unverified. A value must come from the same lookup, scope, edition set and, for a ratio, the denominator field the tool used. Saved 0.6.0 no-call marks read as unverified. Incomplete tool trails stay visibly unverifiable and do not enter later model context |
| Usage shares | Percentages in answers and panels, one decimal; `<0.1%` for small positive shares; missing denominator means unavailable; `per_1000` retained for compatibility |
| HTTP MCP and hosted visitor code | Optional `server` extra, deferred prototypes; no verified public multi-user operation or strong visitor isolation |
| Platforms | Linux/WSL tested; macOS uses the implemented POSIX/default-browser paths but needs a full device check; native Windows unvalidated |

### Released data

The default release is revision **`1e1fa171722da693`**:

| Measure | Value |
|---|---:|
| Conference editions / conferences | 61 / 11 |
| Listed paper-edition observations | 116,988 |
| Matched to arXiv | 88,873 |
| Analyzed paper-edition observations | 88,172 |
| Observations with subject labels | 38,672 |
| Catalog entries | 9,334 |
| Entries with reviewed introduction claims | 8,770 |
| Benchmarks with individually reviewed resource locations | 315 |
| Reviewed resource location entries | 336 |
| Unique paper-provided links / source papers | 70 / 65 |
| Benchmark entries with paper-provided links | 74 |

The 70 links have 79 benchmark-link attachments; these are different counting
units. Introduction and location reviews performed by Claude Code are **AI
reviews**, not human reviews. Expanded editions still have unreviewed
introduction candidates and uneven field-label coverage; do not imply full
discovery coverage from paper-use coverage.

Against data-20261019 (`8503cf1b1f7bee99`), observations, uses, adoption and
claims are unchanged. Reviewed locations grew from 89 benchmarks/89 entries
to 315/336, and paper links were added. Noisy MNIST's paper match and reviewed
claim are restored; an old retained-claim note is not its current state.
CitePretrainBench is restored, and OTIB uses its clean, name-containing
introduction sentence.

### Release verification

0.6.1: CI passed on `de08604` (Python 3.10 and 3.14). Local checks also
passed: 290 Python tests (also with the server extra and on Python 3.10),
Node 12 and the regression figures. A fresh wheel install outside the checkout
passed the smoke test. Data installed from a local URL with its checksum, from
the default URL, and over a home with older data. A saved conversation pinned
to `8503cf1b1f7bee99` continued on that revision. Live questions through
Claude Code and Codex worked. The browser page and keyed API were served from
the installed wheel. Published 0.6.1 hashes (identical on GitHub and PyPI):
wheel `e684e0adccefa4c613790e7d66f0f13eb406b8529f969ef968999c61d08088f1`,
sdist `5717472b69ae2d408119e426b8125b6d0e13d34268bc77b8ec68985278cf6a53`.
A fresh PyPI install downloaded the default data and returned the figures
below.

0.6.0: CI passed on `60873ad`. Python 3.10/3.14 and server-extra checks each passed
257 tests; Node checks passed 10 tests. Published wheel/sdist checksums match
the release assets and PyPI records:

| Artifact | SHA-256 |
|---|---|
| Data bundle | `f7c486a7302e1431bdbe721e3fb0ac9b163fd727c4984bb9716792dceacbc58f` |
| Wheel | `fb087a2a11aa119aae10232703ee3f7a814f70d1369f8c2e51306f9c7c0e35db` |
| Source archive | `116cfca6937891e7c9b0d88ae31e673b1fa721913d767e5937848e4efff89d1c` |

A fresh environment using the published wheel downloaded the default data,
installed `1e1fa171722da693` and passed integrity checks. Verified figures:
robotics/latest 513 analyzed papers, LIBERO/SIMPLER/Meta-World 82/41/29,
LIBERO 16.0%; ICLR CIFAR-10 179/1,894, 197/3,060, 168/4,252 (2024-2026),
shares 9.5%/6.4%/4.0%. Saved conversations keep their previous revision after
an update; a new conversation uses the newly active revision.

## Shared architecture and data rules

### Storage and revisions

- `core/benchmarks.py` owns tool behavior and counting once. The builder
  writes normalized research tables directly to SQLite; `JsonRepository`
  and the permanent JSON/SQLite parity path have been removed.
- The published database contains editions, benchmark identities/aliases,
  claims, paper observations/authors/labels, uses/roles/evidence and unresolved
  attribution. Structured extra fields retain resource and paper-link records.
- Raw HTML archives and intermediate extraction files remain build inputs.
  A separate working database records processing state, attempts, source
  checks, review queues and runs. Those operational records and conversations
  never enter the research revision or its logical content hash.
- Revisions are immutable SQLite files plus manifests. Installation checks
  file checksums and recomputes every supplied logical hash using its named
  rule (`v1-json` or `v2-logical`); unknown rules and contradictory hash fields
  are refused. Old installed manifests are never rewritten to fit new bytes.
- Activation changes one pointer after installation verification. Each
  conversation/tool/answer check is pinned; a missing pinned revision is a
  recovery error, not a silent switch. Stdio MCP pins at process start and
  must be restarted for new active data. Optional HTTP MCP selects by path.

### Identity, counting and evidence

- Paper rows merge only on official IDs or validated feed links. Within a
  feed/edition/track, sourceid and uid must both agree. Whole groups undergo
  author and official-ID conflict checks. Retain raw source IDs, titles and
  schedule rows; titles only generate identity candidates.
- Benchmark IDs are stable and never reused. Preserve versions, settings,
  homonyms and meaningful characters such as `+`. Different papers' products
  merge only under a reviewed `same_product` decision, never author overlap,
  a citation or a shared repository alone. Corrections require an explicit
  reviewed target and evidence.
- Introductions need reviewed source sentences/context. Changed evidence
  makes a review stale. Vanished claims retain history and need a reviewed
  retain/retire decision; a global title threshold is not lowered to repair
  one paper match. Use reviewed individual match decisions instead.
- Usage counts paper-edition observations. Adoption deduplicates paper IDs,
  excludes introducing papers and separates other authors, introducing
  authors involved and unknown relationships; pre-claim use is separate.
- Explicit evaluation/training roles come from the dedicated arXiv HTML
  pass, not mere name mentions, abstracts or background citations. Missing
  text, links, metadata or attribution remains unknown, not zero/non-use.
- Always state edition, role, scope and analyzed denominator. Percentages
  use the same tool result's count and denominator, with shared rounding.
  Trend states use the existing within-venue test (absolute z at least 2.576;
  0 to at least 8 papers is newly observed); z is not a default UI/answer field.
  Differences between revisions for one edition are data/rule changes, not
  year-to-year trends.
- `papers_with_unattributed_benchmark_mentions` and its compatibility alias
  `unattributed_papers` are one diagnostic count: observations with an
  unresolved mention, possibly alongside confirmed use. Neither it nor its
  complement establishes papers with no stated benchmark.
- Preserve label provenance: declared area/subarea, venue track/mapping,
  proceedings volume, derived topic or unknown. A volume ID or unknown origin
  is not a research field. Field filters disclose unlabeled/excluded papers.
- Quotes are verbatim and tied to one paper. Table-cell assemblies are
  identified rather than quoted as original sentences. Numeric checks bind
  benchmark, edition, role, metric and query context from the turn's own log;
  a check is not a quality score or a semantic-correctness guarantee.

### Resource links and enrichment

Verified benchmark locations and `introducing_paper.links` have different
meanings. A paper-provided link records what that paper printed; it never
automatically becomes a verified benchmark resource. Other source papers'
links keep their own paper attribution. Retain original and repaired URLs,
source HTML hash, arXiv version when known, OAI datestamp, element/quote,
reviewer/method and observation time without conflating them.

Confirm resource relationships from the paper's own release statement and
what the destination provides, distinguishing a benchmark release from a
comparison/component mention. A matching name or profile alone is not enough.
Artifact type, verification level and observed data status stay separate.
Repository existence, file lists and named download routes do not validate
records or guarantee download access. Record HTTP failures by date; do not
infer privacy/deletion from 401/404. Unknown dates remain unknown.

Enrichment uses cached local/paper evidence and arXiv metadata first;
OpenAlex supplements metadata, and Tavily proposes candidates. Large paid
search remains deferred. Use only the credentials and budgets authorized for
the task; never silently borrow a globally logged-in account. Dedicated
GitHub enrichment uses `GITHUB_API_TOKEN` by name and only on GitHub API
requests, with rate-limit handling and resumable caches. Keys and sign-in
content never enter logs, browser output or artifacts.

## Incremental pipeline and publication boundary

`src/icml/` implements collection/abstract completion, ID-based normalization,
arXiv harvesting/matching, HTML parsing, role/claim extraction, catalog review
and database building. `python3 -m icml.update status|run|adopt|adopt-rules`
coordinates stages; the installed serving package does not need pipeline
dependencies or raw archives.

- Discovery re-harvests an overlapping OAI window and can re-collect open
  editions. Read the OAI header datestamp as a re-check signal, not a paper
  version; preserve author metadata and reviewed match decisions.
- On a re-check, fetch/parse into staging. Replace a good result only with a
  new successful parse; a no-HTML, thin or failed response preserves the old
  result and records an unavailable/failed attempt. Keep archives by content
  hash; changed HTML and changed parsed text are separate measurements.
- Only actual fetch/re-check attempts advance their timestamps. Old results
  do not acquire the current source version by adoption or unrelated updates.
  The retry period uses the last attempt, not general row modification time.
- Track extraction-code, vocabulary and catalog-code versions separately.
  Review/catalog changes regenerate the catalog first; extract only stages
  stale under the resulting extraction/vocabulary pair. If vocabulary keeps
  changing after the bounded passes, fail without building or publishing.
- `adopt-rules` requires an explicit equivalence reason and named vocabulary;
  it does not overwrite recorded vocabulary or count as a source refresh.
- Review queues retain claim history, re-queue changed/vanished evidence and
  dequeue decided retain/retire cases. Store review actors/methods explicitly.
- A full corpus mention pass was measured at about one hour in run 15; the
  earlier two-hour estimate included two passes. Do not promise fixed timings
  or start a full extraction for documentation, layout or verifier changes.

The **OS timer remains unregistered**. Incremental processing and manual
candidate comparisons are implemented, but an automatic semantic acceptance
gate before activation is still follow-up work. Do not equate integrity
verification with acceptance of changed research content. `--publish` changes
active data; leave it off when the authorized task is candidate preparation.

## Follow-up work

1. **Paper key sentences (0.7.0):** for analyzed papers, three of the authors'
   own sentences: why the work was needed, its key method, its result. They are
   shown as three labeled highlights (not color alone) and called through a
   read-only tool only when asked. Build them from a new extraction over the
   current corpus. The August local-model `facts_*` files cover six editions
   in an older layout and are not reused as data. A model selects the
   sentences, code verifies each one verbatim against the source, and the
   record keeps source, method and date. This is AI selection, not a human
   summary. A paper without them is "not available", never empty. Needs a new
   data release. The plan and pilot results live in ignored work notes.
2. **Verifier context:** 0.6.1 binds values to lookup, scope, edition set and
   denominator field. The checker reads a fixed cue list, so some correct
   numbers stay unverified. Add new cases as fixed-log regressions with
   their correct counterparts. Keep old per-1,000 conversations readable.
3. **Platform checks:** actual macOS and native Windows testing. Existing
   browser automation and the owner's physical-keyboard check cover the
   tested setup, not every platform or screen reader.
4. **Automatic update acceptance:** candidate validation tied to the exact
   database/checksum, expected-change rules and failure behavior before
   unattended activation; then scheduling/recovery. Do not register a timer
   as a side effect of another task.
5. **Retention/leases:** planned, not delivered. Keep recent N plus active,
   referenced and in-use revisions; leases outside immutable directories
   carry host/PID/start identity, with one lock for leases and deletion.
6. **Metadata/records:** benchmark, release and paper-specific setup levels;
   begin GSM8K/MATH-500 with pinned revision/config/split, complete files,
   schema/row/value/checksum validation and retrieval manifests. Existing
   resource links and file observations are not this end-to-end capability.
7. **Configuration hygiene:** distinguish project MCP using the checkout
   (`.mcp.json`) from user MCP using an installed package. Preserve intentional
   development registrations; remove/switch only in the authorized scope.
8. **Deferred deployments:** revisit isolation, provider conditions and
   approvals only if public multi-user hosting is resumed. Exact evaluation
   execution adapters and the VS Code Marketplace are separate later work.

## Working practice, checks and public instructions

Follow [AGENTS.md](AGENTS.md) for commands and invariants. Use repo-relative
paths; installed user data normally lives in `~/.bench2agent` or an explicit
`--home`, while a source checkout can use its own data. Check the command,
module path, home and active revision before comparing installed and checkout
behavior. Do not change another contributor's edits or create an unsolicited
branch/worktree. Keep storage and extraction/counting-rule changes in separate
commits. Run meaningful checks appropriate to the change and report observed
results, environment/revision and remaining limits.

The 0.6.0 data's baseline figures live in `scripts/regression_check.py`; older
revision blocks remain historical fixtures, never current-answer defaults.
Packaging changes need a fresh installed-wheel smoke check outside the
checkout. Releases follow [docs/releases.md](docs/releases.md) and the owner's
authorization. An explicitly authorized documentation push does not publish
a new package/data release or register a timer; do not request the same
routine approval again when it has already been given.

**These two instruction files are now tracked and public**, as requested by
the owner on 2026-10-21. Keep private notes in ignored `todo.md` or ignored
work directories. Never add `.env`, credentials, sign-in content, chats,
database/corpus exports, raw papers, `.claude/`, institutional reports or
session-specific investigation artifacts. Earlier instruction copies remain
in ignored backup directories; only current policy belongs here.

## History

- 2026-10-02: benchmarks became the discovery unit, with papers as evidence.
- 2026-10-07/08: BenchTrend renamed Bench2Agent; shared verified-tool approach.
- 2026-10-13: 61-edition coverage, no-account decision, SQLite/revision/identity
  work; old JSON-era revisions remain supported.
- 2026-10-15: two local interfaces clarified; earlier local-terminal/hosted-web
  split superseded. Source re-check and claim-history corrections followed.
- 2026-10-20: 0.5.0 published with data-20261019. Its tag's timing-test CI issue
  was fixed later on main without moving the tag or changing package behavior.
- 2026-10-21: 0.6.0 and data-20261021 published at `60873ad`; local browser,
  shared history, percentages and separate paper-provided links released.
- 2026-10-21: owner requested synchronized CLAUDE.md/AGENTS.md updates and a
  commit/push; these files became shared public repository instructions.
- 2026-10-22: 0.6.1 published at `de08604` after three Codex reviews of the
  number checker; paper key sentences planned for 0.7.0 with a new extraction.
