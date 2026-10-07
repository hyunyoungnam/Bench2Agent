# BenchTrend

[![PyPI](https://img.shields.io/pypi/v/benchtrend?label=pypi)](https://pypi.org/project/benchtrend/)
[![Code release](https://img.shields.io/github/v/release/hyunyoungnam/BenchTrend?filter=v*&label=release)](https://github.com/hyunyoungnam/BenchTrend/releases)
[![Data release](https://img.shields.io/badge/data-2026--10--07-blue)](https://github.com/hyunyoungnam/BenchTrend/releases/tag/data-20261007)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](docs/terminal.md)

Find which benchmarks researchers evaluate on, how their use changes, and
which new benchmarks other researchers adopt. Ask in a terminal conversation,
or use BenchTrend inside Claude Code or Codex.

Answers include usage counts, the papers covered, and original paper evidence.
**Usage frequency describes adoption, not benchmark quality.**

[Install](#get-started) · [User guide](docs/terminal.md) ·
[Releases](docs/releases.md) · [License](LICENSE)

## Ask a question

Two shortened answers selected after testing four English prompts through MCP
with the **2026-10-07 data snapshot**. Counts were cross-checked against the
returned tool results; model wording can vary.

> **You:** What benchmarks are researchers using in robotics lately?
>
> **BenchTrend:** Among 509 analysed papers labelled robotics, LIBERO appears
> in 80 evaluation papers, SIMPLER in 41, and Meta-World in 29. This covers
> ICLR 2026, ICML 2026, and NeurIPS 2025. Unlabelled papers, including CoRL,
> are outside this field filter.

> **You:** How has CIFAR-10 usage changed at ICLR from 2024 to 2026?
>
> **BenchTrend:** Its stated evaluation use fell from 94.3 to 64.8 to 38.9
> per 1,000 analysed papers: 178 of 1,888 in 2024, 197 of 3,038 in 2025,
> and 165 of 4,237 in 2026. The counts use papers with parsed full text;
> usage in unparsed papers is unknown.

You can also ask which newly introduced benchmarks other authors use, then
ask for the evidence behind a result. In the standalone terminal, `/sources`
shows the original sentences and paper links. Quotes and figures are checked;
anything the checker cannot verify is marked in the answer.

## Get started

### Terminal conversation — macOS / Linux

The short commands below require **BenchTrend 0.2.1 or later**. Until 0.2.1
is published, install the latest source and start it:

```bash
uv tool install https://github.com/hyunyoungnam/BenchTrend/archive/refs/heads/main.tar.gz
benchtrend
```

Or install the same source with `pip` in your Python environment:

```bash
python -m pip install https://github.com/hyunyoungnam/BenchTrend/archive/refs/heads/main.tar.gz
benchtrend
```

From a local checkout, use `uv tool install .` or `python -m pip install .`.

Once 0.2.1 is published, install from PyPI using
[uv](docs/terminal.md#install-uv):

```bash
uv tool install 'benchtrend>=0.2.1'
benchtrend
```

Or using `pip`:

```bash
python -m pip install 'benchtrend>=0.2.1'
benchtrend
```

`uv tool install` creates a separate environment for the app. `pip` installs
into your active Python environment; a virtual environment is recommended.

The first launch downloads the **16 MB research-data snapshot** and checks
its checksum automatically. Later launches reuse your installed data.
This snapshot contains benchmark usage and paper evidence; external
benchmarks' underlying questions, labels, and images are separate downloads.

Choose OpenAI or Anthropic at the prompt, accept or change the model, and
enter your API key when asked. The key is hidden and used only for that
session. Existing `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` environment variables
also work. API access uses your provider's API billing, separately from a
ChatGPT or Claude subscription.

Once installed, just run `benchtrend` to return. No GPU is needed.
[Windows setup, saved conversations, and data updates](docs/terminal.md).

### Use your existing Claude Code or Codex login

BenchTrend can supply the same data to your AI client's conversation through
MCP. The client handles model access through its own login. BenchTrend's MCP
server needs no separate API key.

After installing BenchTrend, open a conversation in your preferred client.
Claude Code or Codex must already be installed and signed in:

```bash
benchtrend claude
```

```bash
benchtrend codex
```

These commands download the data if needed and open a new session with
BenchTrend tools available. Ask the client to **use BenchTrend** for your
benchmark question.
For a fresh installation using this route, follow the
[complete MCP setup](docs/terminal.md#install-for-claude-code-or-codex).
Your client writes its answers; BenchTrend's final-answer checks run in the
standalone terminal interface.

## Data coverage

The current snapshot covers **28 editions of 10 conferences**, with
**58,616 parsed paper-edition observations** and **9,332 benchmark and dataset
entries**. Of those entries, 8,768 have reviewed introduction claims inside
this corpus ([review rubric](docs/introduced-review.md)).

| Venue | Editions | Venue | Editions |
|---|---|---|---|
| ICML | 2024 · 2025 · 2026 | ICCV | 2023 · 2025 |
| ICLR | 2024 · 2025 · 2026 | ECCV | 2024 · 2026 |
| NeurIPS | 2023 · 2024 · 2025 | ACL | 2024 · 2025 · 2026 |
| CVPR | 2024 · 2025 · 2026 | EMNLP | 2023 · 2024 · 2025 |
| AAAI | 2024 · 2025 · 2026 | CoRL | 2023 · 2024 · 2025 |

Counts come from explicitly stated evaluation or training use in parsed arXiv
HTML. Each answer reports its scope and denominator. Missing full text or an
unstated role does not establish that a benchmark was unused. Field filters
cover papers with matching labels and can exclude unlabelled venues.

“New” means a reviewed introduction claim inside this corpus, which starts
in 2023; it does not establish the first public release. Earlier observed use,
self-use, use by other authors, and uncertain attribution are recorded
separately. Recent introductions have less time to accumulate adoption.

BenchTrend is a research prototype. Released snapshots have fixed identities,
and conversations record the snapshot used for their answers.
[Data rules and tool details](docs/benchmark-chat.md).

## Releases and development

Code and data are released separately. [v0.2.0](https://github.com/hyunyoungnam/BenchTrend/releases/tag/v0.2.0)
is the code — wheel, source distribution and checksums, the same files
published on [PyPI](https://pypi.org/project/benchtrend/) — and [data-20261007](https://github.com/hyunyoungnam/BenchTrend/releases/tag/data-20261007)
is the snapshot. `uv tool upgrade benchtrend` picks up later code releases;
data updates are a separate `benchtrend data install`. See
[how releases work](docs/releases.md).

The earlier paper-reading interface remains available as an evidence surface:
[browser guide](docs/browser.md). Its compatibility command is `bellwether`.
Paper-view principles are in [docs/paper-view.md](docs/paper-view.md).

BenchTrend's code is licensed under [Apache-2.0](LICENSE).
