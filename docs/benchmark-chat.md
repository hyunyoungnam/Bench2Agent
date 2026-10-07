# Benchmark conversation

BenchTrend answers benchmark questions through its terminal conversation and
the same local Python MCP tools in Codex, Claude Code and the existing VS Code
prototype. See [terminal usage](terminal.md) for installation and registration.
The agent explains results;
Python computes counts and retains the paper sentences. Frequency is not a
quality measure.

## Build and serve

After the full-text mention run and reviewed identity catalogue are stable:

```bash
PYTHONPATH=src python3 -m bellwether benchmarks
PYTHONPATH=src python3 -m bellwether serve --no-meili
```

The exporter joins `mentions_<edition>.jsonl`, normalized paper metadata,
arXiv mappings, available topic labels and the reviewed catalogue. It writes
`data/processed/benchmark_snapshot.json` atomically and rejects source changes
during export. The snapshot travels in `bellwether bundle`; neither the corpus
nor that artifact belongs in Git. `/benchmarks/status` exposes coverage.

The snapshot is self-contained for benchmark queries. Unreviewed provisional
names remain unresolved observations rather than ranked entities. Same-name
claims from the same introducing paper are deduplicated in the snapshot with
all evidence and former IDs retained; unrelated introducing papers remain
distinct. Ambiguous use requires a local citation to exactly one introducing
paper. This adapter does not edit the source catalogue or extraction outputs.

## Question routing

| Question | Tools | Checks |
|---|---|---|
| What do researchers test on now in a field? | `benchmark_scope`, `benchmark_usage` | Exact field label; latest analysed edition per venue; evaluation role; denominator and verbatim evidence |
| Is a benchmark becoming more or less common? | `benchmark_trend` | Per-1,000 parsed-paper rates; comparisons within a venue; observed zeros separated from missing scope |
| Which benchmarks are new? | `new_benchmarks` | Reviewed claims, including body-only names and entries without subsequent uptake; evaluation benchmarks separate from training resources |
| Are other researchers adopting one? | `benchmark_adoption` | Other/self/undetermined author names; evaluation/training separate; introducing papers excluded; unresolved homonyms reported |
| Which introduced benchmarks have the most uptake? | `new_benchmarks(sort="adoption")` | External evaluation use, or training use for datasets; counts across all installed editions; introduction filters do not filter using papers; prior-use flags retained |
| Which experiments support that count? | `benchmark_evidence` | Paper ID, URL, edition, written name and verbatim role sentences |

`benchmark_info` remains the older abstract-name inventory and location lookup.
It does not establish evaluation use. Existing paper and dataset CSV endpoints
retain their original meaning: inventories are not dataset observation exports.

All successfully parsed papers, including those without a stated benchmark,
enter each edition's denominator. Pooled usage counts are paper-edition
observations; adoption deduplicates stable arXiv paper IDs across editions,
merges roles and uses the earliest observed edition. Field filters cover only
papers with matching declared topic/area/subarea labels. Some venues have no
such labels; zero matches there do not establish absence of that field. The
scope response gives accepted, arXiv-matched, parsed and field-labelled totals.

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
PYTHONPATH=src python3 -m bellwether datasets --out /tmp/bellwether-datasets
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
