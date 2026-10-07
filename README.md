# BenchTrend

[![PyPI](https://img.shields.io/pypi/v/benchtrend?label=pypi)](https://pypi.org/project/benchtrend/)
[![Code release](https://img.shields.io/github/v/release/hyunyoungnam/BenchTrend?filter=v*&label=release)](https://github.com/hyunyoungnam/BenchTrend/releases)
[![Data release](https://img.shields.io/badge/data-2026--10--07-blue)](https://github.com/hyunyoungnam/BenchTrend/releases/tag/data-20261007)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/hyunyoungnam/BenchTrend/blob/v0.2.1/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://github.com/hyunyoungnam/BenchTrend/blob/v0.2.1/docs/terminal.md)

Find which benchmarks researchers evaluate on, how their use changes, and
which new benchmarks other researchers adopt. Ask in a terminal conversation,
or use BenchTrend inside Claude Code or Codex.

Answers include usage counts, the papers covered, and original paper evidence.
**Usage frequency describes adoption, not benchmark quality.**

[Install](#get-started) · [User guide](https://github.com/hyunyoungnam/BenchTrend/blob/v0.2.1/docs/terminal.md) ·
[Releases](https://github.com/hyunyoungnam/BenchTrend/blob/main/docs/releases.md) · [License](https://github.com/hyunyoungnam/BenchTrend/blob/v0.2.1/LICENSE)

## Ask a question

Two shortened answers selected after testing four English prompts through MCP
with the **2026-10-07 data snapshot**. Counts were cross-checked against the
returned tool results; model wording can vary.

> **You:** What benchmarks are researchers using in robotics lately?
>
> **BenchTrend:** Among 509 analysed papers labelled robotics,
> [LIBERO](https://github.com/Lifelong-Robot-Learning/LIBERO) appears in 80
> evaluation papers ([introducing paper](https://arxiv.org/abs/2306.03310)),
> [SIMPLER](https://github.com/simpler-env/SimplerEnv) in 41
> ([introducing paper](https://arxiv.org/abs/2405.05941)), and
> [Meta-World](https://github.com/Farama-Foundation/Metaworld) in 29. This covers
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

Answers include repository, dataset or homepage links and reviewed introducing
papers where available. Locations are our mapping; a repository link does not
guarantee a direct data download. Missing links mean no confirmed location.

## Get started

### Terminal conversation — macOS / Linux

With Python 3.10+ in your environment, install and start BenchTrend:

```bash
python -m pip install benchtrend
benchtrend
```

<details>
<summary>Alternative: install with uv</summary>

Choose this instead of pip. With [uv installed](https://github.com/hyunyoungnam/BenchTrend/blob/v0.2.1/docs/terminal.md#install-uv):

```bash
uv tool install benchtrend
benchtrend
```

</details>

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
[Windows setup, saved conversations, and data updates](https://github.com/hyunyoungnam/BenchTrend/blob/v0.2.1/docs/terminal.md).

### Use your existing Claude Code or Codex login

BenchTrend can supply the same data to your AI client's conversation through
MCP. The client handles model access through its own login. BenchTrend's MCP
server needs no separate API key.

After installing BenchTrend, open a conversation in your preferred client.
Claude Code or Codex must already be installed and signed in:

| Command | Model access |
|---|---|
| `benchtrend` | OpenAI or Anthropic API key, with separate API billing |
| `benchtrend claude` | Your existing Claude Code login |
| `benchtrend codex` | Your existing Codex login |

These commands download the data if needed and open a new session with
BenchTrend tools available. Ask the client to **use BenchTrend** for your
benchmark question.
For a fresh installation using this route, follow the
[complete MCP setup](https://github.com/hyunyoungnam/BenchTrend/blob/v0.2.1/docs/terminal.md#install-for-claude-code-or-codex).
Your client writes its answers; BenchTrend's final-answer checks run in the
standalone terminal interface.

## Data coverage

The current snapshot covers **28 editions of 10 conferences**, with
**58,616 parsed paper-edition observations** and **9,332 benchmark and dataset
entries**. Of those entries, 8,768 have reviewed introduction claims inside
this corpus ([review rubric](https://github.com/hyunyoungnam/BenchTrend/blob/v0.2.1/docs/introduced-review.md)).

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
[Data rules and tool details](https://github.com/hyunyoungnam/BenchTrend/blob/v0.2.1/docs/benchmark-chat.md).

## Releases and development

Code and data are released separately. [v0.2.1](https://github.com/hyunyoungnam/BenchTrend/releases/tag/v0.2.1)
is the code — wheel, source distribution and checksums, the same files
published on [PyPI](https://pypi.org/project/benchtrend/) — and [data-20261007](https://github.com/hyunyoungnam/BenchTrend/releases/tag/data-20261007)
is the snapshot. Update code with `python -m pip install --upgrade benchtrend`
or `uv tool upgrade benchtrend`, using the method you installed with;
data updates are a separate `benchtrend data install`. See
[how releases work](https://github.com/hyunyoungnam/BenchTrend/blob/main/docs/releases.md).

The earlier paper-reading interface remains available as an evidence surface:
[browser guide](https://github.com/hyunyoungnam/BenchTrend/blob/v0.2.1/docs/browser.md). Its compatibility command is `bellwether`.
Paper-view principles are in [docs/paper-view.md](https://github.com/hyunyoungnam/BenchTrend/blob/v0.2.1/docs/paper-view.md).

BenchTrend's code is licensed under [Apache-2.0](https://github.com/hyunyoungnam/BenchTrend/blob/v0.2.1/LICENSE).
