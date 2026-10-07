# BenchTrend

Which benchmarks does the field evaluate on right now, how is that changing
from one conference edition to the next, and which newly introduced
benchmarks are other people picking up? BenchTrend answers from the papers
themselves: every count is a count of papers whose own sentence says "we
evaluate on …" or "we introduce …", and every count opens onto those
sentences. **Counts describe use, not quality.**

Ask it in a terminal conversation, or give its tools to Claude Code or Codex.

## What an answer looks like

Figures from the released data (snapshot `0dd4b9bb`, 2026-10-07), each with
the tool call that produced it. "Per 1,000" is per 1,000 papers whose full
text was parsed in that scope — the denominator is always printed.

| Question | Tool | Answer |
|---|---|---|
| What do papers evaluate on now? | `benchmark_usage(latest=true)` — the latest edition of each of the ten venues, 26,727 parsed papers | GSM8K 40.3 per 1,000 · CIFAR-10 31.3 · MMLU 28.8 · COCO 27.4 · MATH-500 24.6 |
| …in robotics? | `benchmark_usage(topic="robotics", latest=true)` — 509 papers that declare the field | LIBERO 157.2 · SIMPLER 80.5 · Meta-World 57.0 · RLBench 45.2 · CALVIN 41.3 |
| Is CIFAR-10 falling at ICLR? | `benchmark_trend(benchmark="cifar-10", venue="iclr")` | 94.3 → 64.8 → 38.9 per 1,000 over ICLR 2024 → 2025 → 2026 (178 of 1,888; 197 of 3,038; 165 of 4,237). Both steps pass the change test (z = −3.8, −5.0) |
| Which new benchmarks are others adopting? | `new_benchmarks(sort="adoption")` | MMMU, first claimed at CVPR 2024: evaluated on by 328 later papers with none of its authors, and by 9 with |

In conversation the answer is prose; `/sources` lists every quoted sentence
with a ✓ or ✗ from a check against the paper's text, and every figure is
recomputed from the tool call it cites.

## Install

```bash
uv tool install git+https://github.com/hyunyoungnam/BenchTrend
benchtrend data install --url https://github.com/hyunyoungnam/BenchTrend/releases/download/data-20261007/benchtrend-data.tar.gz \
    --sha256 d19dcc2c47cc13738188176b5b87534e7e3eb0f0d11c86731bf41e0a1acca50e
benchtrend status
```

Python 3.10+; standard library only, so Windows works natively (PowerShell).
The package is not on PyPI. Then one of two modes:

| | Terminal conversation | Inside an AI client |
|---|---|---|
| Start | `benchtrend init --provider anthropic` then `benchtrend` | `benchtrend mcp --connect claude` or `--connect codex`, then a new session |
| Model access | your `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` | the client's own login — no API key |
| Verification | quotes and figures checked before the answer is shown; `/sources` | the client writes the answer; the tools return verbatim sentences and denominators |

`benchtrend status` says which of these is ready. Details — saved
conversations, data updates, other MCP clients — in
[docs/terminal.md](docs/terminal.md); the six tools, question routing and
verification in [docs/benchmark-chat.md](docs/benchmark-chat.md).

## What the data is

Ten venues, the three most recent editions each (two for ICCV and ECCV,
which alternate years):

| Venue | Editions | | Venue | Editions |
|---|---|---|---|---|
| ICML | 2024 · 2025 · 2026 | | ICCV | 2023 · 2025 |
| ICLR | 2024 · 2025 · 2026 | | ECCV | 2024 · 2026 |
| NeurIPS | 2023 · 2024 · 2025 | | ACL | 2024 · 2025 · 2026 |
| CVPR | 2024 · 2025 · 2026 | | EMNLP | 2023 · 2024 · 2025 |
| AAAI | 2024 · 2025 · 2026 | | CoRL | 2023 · 2024 · 2025 |

58,616 papers with parsed full text; 9,332 benchmarks and datasets, 8,768 of
them introduced by a paper inside this corpus, each introduction claim
reviewed one by one ([the rubric](docs/introduced-review.md)). A benchmark is
its published name: `MATH` and `MATH-500` are two rows, `CIFAR10` and
`CIFAR-10` are one, and a relation between two is shown but never merges a
count.

## What it does not say

- **Only stated use is counted.** A paper counts for a benchmark when its
  own sentence or table caption says it evaluates (or trains) on it. Not
  stating is not "not used".
- **Only papers with full text, from arXiv.** 71–91% of each edition is
  matched and parsed; AAAI is 63–66%. The unmatched papers are absent from
  every count, never counted as non-users, and the denominator beside each
  figure is the parsed papers in that scope.
- **"New" means first claimed in this corpus, which starts in 2023.** MME
  shows a first claim at NeurIPS 2025 with 82 earlier uses on record; the
  `before_claim` figure is reported so that case is visible.
- **A field filter covers papers that declare the field.** Some venues
  carry no such labels (CoRL, for one), so a field query can exclude a venue
  entirely; the scope reports how many papers it covered.
- **Frequency is not quality.** No score, no "best", no ranking of
  benchmarks by anything but how many papers use them.
- **Research prototype.** Counts change when the data is re-cut; a
  conversation records the snapshot it was answered against, and released
  snapshots are tagged.

## Also in this repository

The earlier paper-reading product — a local site for one conference edition
with search, topics, verified sentences and a quote-checked chat — is still
here as the evidence surface: [docs/browser.md](docs/browser.md). It keeps the
project's earlier name, `bellwether`, for its command and install directory;
`WNAI_*` environment variables are older still. Build documentation, data
traps and measured quality are in `CLAUDE.md`; the paper-reading principles in
[docs/paper-view.md](docs/paper-view.md).

Apache-2.0.
