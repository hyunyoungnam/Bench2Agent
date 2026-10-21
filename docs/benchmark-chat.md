# Benchmark conversation

Bench2Agent answers benchmark questions through its terminal conversation and
the same local Python MCP tools in Codex, Claude Code and the existing VS Code
prototype. See [terminal usage](terminal.md) for installation and registration.
The agent explains results;
Python computes counts and retains the paper sentences. Frequency is not a
quality measure.

## Build and serve

After the full-text mention run and reviewed identity catalog are stable:

```bash
PYTHONPATH=src python3 -m bench2agent benchmarks
PYTHONPATH=src python3 -m bench2agent serve --no-meili
```

The builder joins `mentions_<edition>.jsonl`, normalized paper metadata,
arXiv mappings, available topic labels and the reviewed catalog, and writes
the research tables straight into `data/processed/benchmark_snapshot.sqlite`
(built beside the target and swapped in once checked); it rejects source
changes during the build. The revision id is a hash of that content.
`bench2agent benchmarks --publish` installs it; `bench2agent data bundle`
packs it. Neither the corpus nor those artifacts belong in Git.
`/benchmarks/status` exposes coverage.

The database is self-contained for benchmark queries. Unreviewed provisional
names remain unresolved observations rather than ranked entities. Same-name
claims from the same introducing paper are deduplicated in the snapshot with
all evidence and former IDs retained; unrelated introducing papers remain
distinct. Ambiguous use requires a local citation to exactly one introducing
paper. This adapter does not edit the source catalog or extraction outputs.

## Question routing

| Question | Tools | Checks |
|---|---|---|
| What do researchers test on now in a field? | `benchmark_scope`, `benchmark_usage` | Exact field label; latest analyzed edition per venue; evaluation role; denominator and verbatim evidence |
| Is a benchmark becoming more or less common? | `benchmark_trend` | Percentages of analyzed papers; comparisons within a venue; observed zeros separated from missing scope |
| Which benchmarks are new? | `new_benchmarks` | Reviewed claims, including body-only names and entries without subsequent uptake; evaluation benchmarks separate from training resources |
| Are other researchers adopting one? | `benchmark_adoption` | Other/self/undetermined author names; evaluation/training separate; introducing papers excluded; unresolved homonyms reported |
| Which introduced benchmarks have the most uptake? | `new_benchmarks(sort="adoption")` | External evaluation use, or training use for datasets; counts across all installed editions; introduction filters do not filter using papers; prior-use flags retained |
| Which experiments support that count? | `benchmark_evidence` | Paper ID, URL, edition, written name and verbatim role sentences |

`benchmark_info` remains the older abstract-name inventory and location lookup.
It does not establish evaluation use. Existing paper and dataset CSV endpoints
retain their original meaning: inventories are not dataset observation exports.

Usage and trend shares use `share_percent`: percent of the analyzed papers
in that query/edition, rounded to one decimal (halves up). Terminal prose and
the browser both use `%`; the raw use count and denominator stay visible.
A missing/zero denominator gives no percentage; a positive share below 0.1%
is displayed as `<0.1%`, with its count. `per_1000` remains a compatibility
field for older clients and saved results, not the default answer unit.
Verification derives percentages only from a benchmark's count and its own
query/edition denominator; it never combines figures from separate calls.

All successfully parsed papers, including those without a stated benchmark,
enter each edition's denominator. Pooled usage counts are paper-edition
observations; adoption deduplicates stable arXiv paper IDs across editions,
merges roles and uses the earliest observed edition. Field filters match
labels that name a research field, each carrying its origin:
`declared_area` / `declared_subarea` (the primary area the authors chose on
the submission form; ICML, ICLR, NeurIPS), `venue_track_mapped` (the topic an
AAAI track name states; the raw track is kept as `venue_track`), and
`derived_topic` (from the abstract pass under a fixed vocabulary; six
editions). Proceedings volume ids (`venue_volume`, the ACL Anthology) are
kept as labels but are not fields. Venue tracks are the venue's
classification, not an author declaration. Some venues carry no field
labels at all; zero matches there do not establish absence of that field.
The scope response gives accepted, arXiv-matched, parsed and field-labeled
totals, the papers per label origin, and each field's origins.

In a query's `coverage`, `papers_with_unattributed_benchmark_mentions`
counts **paper-edition observations with at least one unresolved benchmark
mention**: an unreviewed name or a homonym whose identity could not be
attributed. `unattributed_papers` is retained as a compatibility alias for
the same count. This diagnostic spans all benchmark names and roles in the
selected scope, including when the query asks only for evaluation use.
It overlaps confirmed use: a paper may evaluate on a known benchmark and
also mention an unresolved one. It does not count papers with no stated
benchmark, papers not using a benchmark, or papers missing field labels.
Do not add the alias to the canonical field, subtract it from the parsed
denominator to infer non-use, or infer non-use from a limited list of
benchmark rows. A specific benchmark's `unattributed_using_papers` is a
different metric: unique using papers, not scope-wide observations.

A year-over-year trend is a comparison inside one data revision. The same
edition can differ between revisions because the data or the rules changed
(an identity fix, a field-label rule, a reparse); such a difference is a
revision change, never a usage trend. A field filter's denominator depends
on the revision's label coverage.

Trend states follow the repository rule: within-venue two-proportion z with
absolute z at least 2.576; zero to at least eight papers is `appearing`.
These are descriptive flags, not causal claims or a guarantee of significance
after multiple comparisons. Same-year introduction/use ordering is unknown.
`first_claim` is first reviewed introduction in this corpus, not global priority.
Author matching uses normalized names, not verified researcher identity.

## Answer verification

The system prompt requires tools before answering and discloses unavailable
snapshots rather than reporting zero. Agents cite usage/claim sentences with
`⟦arxiv:2301.00001|verbatim sentence⟧`. The server verifies the exact sentence
against the snapshot, then renders a citation to the paper with the sentence
in its tooltip. Existing numeric paper-ID anchors still work.
Assembled table-cell records remain evidence for counts but cannot receive a
verbatim sentence check.

Benchmark statistic anchors use the complete JSON call arguments, for example
`⟦benchmark_usage:{"topic":"robotics","role":"evaluates_on"}|figures⟧`.
The tool trail retains those arguments and the answer records the snapshot ID.
Codex receives `default_tools_approval_mode="writes"` for this MCP server, whose
tools advertise `readOnlyHint=true`; other servers and the user's global config
are unchanged. See the [official MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
The verifier recomputes statistics, excluding digits in names, paper IDs, URLs
and quotes. A matched number/quote does not verify the surrounding prose's
interpretation; the agent can still overstate scope or draw a poor conclusion.

## Validation

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m bench2agent datasets --out /tmp/bench2agent-datasets
node --test vscode-extension/test/*.test.js
python3 vscode-extension/build_vsix.py
```

`tests/test_benchmark_chat.py` uses contrasting synthetic records for the four
question types, absent snapshots, homonyms, duplicate introducing claims,
duplicate using papers, unknown authors and fabricated quotes. It also checks
that role/scope arguments survive numeric recomputation and that authentication
status never exposes tokens. HTTP checks preserve inventory downloads and
the paper explorer. Live agent answers should be checked separately against
the installed snapshot; fixture tests alone do not assess model behavior.
