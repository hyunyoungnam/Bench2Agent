# Bench2Agent

[![Code release](https://img.shields.io/github/v/release/hyunyoungnam/Bench2Agent?filter=v*&label=release)](https://github.com/hyunyoungnam/Bench2Agent/releases)
[![PyPI](https://img.shields.io/pypi/v/bench2agent)](https://pypi.org/project/bench2agent/)
[![Data release](https://img.shields.io/badge/data-2026--10--07-blue)](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/data-20261007)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/hyunyoungnam/Bench2Agent/blob/main/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/terminal.md)

Find which benchmarks researchers evaluate on, how their use changes, and
which new benchmarks other researchers adopt. Ask in a terminal conversation,
or use Bench2Agent inside Claude Code or Codex.

Answers include usage counts, the papers covered, and original paper evidence.
**Usage frequency describes adoption, not benchmark quality.**

[Install](#get-started) · [User guide](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/terminal.md) ·
[Releases](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/releases.md) · [License](https://github.com/hyunyoungnam/Bench2Agent/blob/main/LICENSE)

## Ask a question

Two shortened answers selected after testing four English prompts through MCP
with the **2026-10-07 data snapshot**. Counts were cross-checked against the
returned tool results; model wording can vary.

> **You:** What benchmarks are researchers using in robotics lately?
>
> **Bench2Agent:** Among 509 analysed papers labelled robotics,
> [LIBERO](https://github.com/Lifelong-Robot-Learning/LIBERO) appears in 80
> evaluation papers ([introducing paper](https://arxiv.org/abs/2306.03310)),
> [SIMPLER](https://github.com/simpler-env/SimplerEnv) in 41
> ([introducing paper](https://arxiv.org/abs/2405.05941)), and
> [Meta-World](https://github.com/Farama-Foundation/Metaworld) in 29. This covers
> ICLR 2026, ICML 2026, and NeurIPS 2025. Unlabelled papers, including CoRL,
> are outside this field filter.

> **You:** How has CIFAR-10 usage changed at ICLR from 2024 to 2026?
>
> **Bench2Agent:** Its stated evaluation use fell from 94.3 to 64.8 to 38.9
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

With Python 3.10+ in your environment, install and start Bench2Agent.
Choose pip or the uv alternative below:

```bash
python -m pip install bench2agent
bench2agent
```

<details>
<summary>Alternative: install with uv</summary>

Choose this instead of pip. With [uv installed](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/terminal.md#install-uv):

```bash
uv tool install bench2agent
bench2agent
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

Once installed, just run `bench2agent` to return. No GPU is needed.
[Windows setup, saved conversations, and data updates](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/terminal.md).

### Use your existing Claude Code or Codex login

Bench2Agent can supply the same data to your AI client's conversation through
MCP. The client handles model access through its own login. Bench2Agent's MCP
server needs no separate API key.

After installing Bench2Agent, open a conversation in your preferred client.
Claude Code or Codex must already be installed and signed in:

| Command | Model access |
|---|---|
| `bench2agent` | OpenAI or Anthropic API key, with separate API billing |
| `bench2agent claude` | Your existing Claude Code login |
| `bench2agent codex` | Your existing Codex login |

These commands download the data if needed and open a new session with
Bench2Agent tools available. Ask the client to **use Bench2Agent** for your
benchmark question.
For a fresh installation using this route, follow the
[complete MCP setup](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/terminal.md#install-for-claude-code-or-codex).
Your client writes its answers; Bench2Agent's final-answer checks run in the
standalone terminal interface.

## Web service direction

`bench2agent serve` starts the existing local browser interface. A hosted
conversation service is planned around the same benchmark tools, with a
relational database, per-user conversations and a separate collection worker.
See the [database and hosting design](docs/database-and-hosting.md) for the
terminal, web and future Mac server architecture.

## Data coverage

The current snapshot covers **28 editions of 10 conferences**, with
**58,616 parsed paper-edition observations** and **9,332 benchmark and dataset
entries**. Of those entries, 8,768 have reviewed introduction claims inside
this corpus ([review rubric](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/introduced-review.md)).

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

Bench2Agent is a research prototype. Released snapshots have fixed identities,
and conversations record the snapshot used for their answers.
[Data rules and tool details](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/benchmark-chat.md).

## Releases and development

Code and data are distributed separately. Bench2Agent 0.3.0 is available on
[PyPI](https://pypi.org/project/bench2agent/0.3.0/) and as a
[GitHub release](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/v0.3.0).
Earlier code tags retain the installation artifacts published before the
rename. The [data-20261007 release](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/data-20261007)
is the current research snapshot. Update code with `uv tool upgrade bench2agent`
or `python -m pip install --upgrade bench2agent`; data updates use
`bench2agent data install`. Existing users can follow the
[migration guide](docs/terminal.md#switch-from-an-earlier-installation). See
[how releases work](docs/releases.md).

The earlier paper-reading interface remains available as an evidence surface:
[browser guide](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/browser.md). Start it with `bench2agent serve`.
Paper-view principles are in [docs/paper-view.md](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/paper-view.md).

Bench2Agent's code is licensed under [Apache-2.0](https://github.com/hyunyoungnam/Bench2Agent/blob/main/LICENSE).
