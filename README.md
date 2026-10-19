# Bench2Agent

[![Code release](https://img.shields.io/github/v/release/hyunyoungnam/Bench2Agent?filter=v*&label=release)](https://github.com/hyunyoungnam/Bench2Agent/releases)
[![PyPI](https://img.shields.io/pypi/v/bench2agent)](https://pypi.org/project/bench2agent/)
[![Data release](https://img.shields.io/badge/data-2026--10--13-blue)](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/data-20261013)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/hyunyoungnam/Bench2Agent/blob/main/LICENSE)

Ask which benchmarks researchers evaluate on, how usage changes across years,
and which new benchmarks other researchers adopt. Bench2Agent connects your
signed-in Claude Code or Codex to tested benchmark tools and checks answers
against the tool results and original paper evidence.

**Usage frequency describes adoption, not benchmark quality.**

[Install](#get-started) · [User guide](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/terminal.md) · [Releases](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/releases.md)

## Get started

You need **Python 3.10+** and **Claude Code or Codex installed and signed in**
on your computer. Bench2Agent and the official AI CLI are separate installations.
If needed, sign in using `claude auth login` or `codex login` first.

Then install and start the terminal conversation:

```bash
python -m pip install --upgrade bench2agent
bench2agent
```

Use an active Python environment; a virtual environment is recommended. On
macOS/Linux, [uv](https://docs.astral.sh/uv/getting-started/installation/)
provides an alternative isolated installation:

```bash
uv tool install --upgrade bench2agent
bench2agent
```

Choose **one** installation method. On the first interactive launch,
Bench2Agent automatically downloads the research-data bundle (about 63 MB),
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
revision after updates. The supported, tested terminal platform is Linux/WSL;
macOS follows the same POSIX path. Native Windows CLI cancellation has not yet
been validated.

API access is an explicit alternative: `bench2agent --connector api` prompts
for the provider and a hidden, session-only API key. API billing is separate
from a Claude or ChatGPT subscription.

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

The **2026-10-13 data release** contains **61 editions of 11 conferences**,
**87,048 parsed paper-edition observations** and **9,332 benchmark/dataset
entries**, including 8,768 entries with reviewed introduction claims.
Revision: `e41da62f2fb3616f`.

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
HTML, with the analysed editions and denominator reported for each query.
Missing or unparsed papers remain unknown. Field filters match labels that
name a research field, each with its origin (the primary area the authors
declared at submission, the topic an AAAI track states, or a derived topic);
proceedings volume ids are kept but are not fields, and many editions carry
no field labels at all, so a field filter excludes those papers. New
introduction claims in the expanded editions are not yet reviewed, so
introduction discovery is less complete than usage coverage. “New” means a
reviewed claim in this corpus, not the first public release worldwide.

A data revision is one SQLite database, identified by a hash of its content;
the released 2026-10-13 bundle still carries the JSON it was built from, which
installs through a one-time conversion. Each conversation and its tools and
checks stay on one immutable revision.
External benchmarks' actual questions, labels and images are separate downloads.
[Data rules](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/benchmark-chat.md) · [Database design](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/database-and-hosting.md)

## Updates and web development

Version **0.4.0** is distributed on [PyPI](https://pypi.org/project/bench2agent/0.4.0/)
and [GitHub](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/v0.4.0).
Update code using your installation method; update research data separately
with `bench2agent data install`. Earlier installed revisions remain available
for saved chats. [Migration and data management](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/terminal.md).

The interface plan is **terminal first, then a designed local browser
workspace** using the same AI CLI login, tools, research data and verifier.
The browser will run on your PC; a dedicated paid web backend is not required.
Its launcher and shared-history integration are planned, not available in
this release. [Browser interface plan](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/browser-interface.md).

The existing optional web/HTTP MCP modules are development prototypes. The
earlier hosted visitor prototype is deferred; hosted sign-in, strong visitor
isolation and public operation remain unvalidated. This release provides the
**local terminal and MCP**.
[Account connections](https://github.com/hyunyoungnam/Bench2Agent/blob/main/docs/account-connections.md).

Code is licensed under [Apache-2.0](https://github.com/hyunyoungnam/Bench2Agent/blob/main/LICENSE).
