# BenchTrend

Ask which benchmarks researchers evaluate on, how usage changes across conference
editions, and which newly introduced benchmarks other researchers adopt.
Answers carry computed counts, dataset coverage and original paper evidence.
Usage frequency describes adoption; it does not measure benchmark quality.

Python 3.10+ is required. The short setup and client launch commands require
BenchTrend 0.2.1 or later; see [0.2.0 setup](#benchtrend-020) for older installs.
The runtime uses only Python's standard library;
no GPU, search engine, VS Code extension or extraction pipeline is needed.

## Install

For a copy-and-paste macOS/Linux installation that includes data and starts
the terminal conversation, use the [README quick start](../README.md#get-started).
For an existing AI client login, use the
[complete MCP setup](#install-for-claude-code-or-codex) below.

From PyPI, from a tagged source archive, or from a checkout:

```bash
uv tool install 'benchtrend>=0.2.1'
# A specific tag, without PyPI:
uv tool install https://github.com/hyunyoungnam/BenchTrend/archive/refs/tags/v0.2.0.tar.gz
# The development branch, or a checkout:
uv tool install git+https://github.com/hyunyoungnam/BenchTrend
uv tool install .
# Alternatively, in a virtual environment:
python -m pip install .
```

If the browser installation (`install.sh`) is already on this machine, it has
put a `bellwether` command on PATH, and `uv` will stop with "Executables
already exist". Add `--force`; the installed package provides the same
`bellwether` command.

An installable wheel can be shared without the source checkout:

```bash
uv tool install ./benchtrend-0.2.1-py3-none-any.whl
```

The wheel includes code, not the research corpus. It runs from any working
directory. Installed runtime data normally lives in `~/.benchtrend`; a source
checkout uses its existing data. An existing `~/.bellwether` snapshot is reused
when there is no new installation directory. Set `BENCHTREND_HOME` or pass
`--home DIR` to choose a different data directory. Code updates preserve data
and conversations.

The package is published on [PyPI](https://pypi.org/project/benchtrend/) as
`benchtrend`; `uv tool upgrade benchtrend` moves to a newer code release.

### Install uv

If `uv` is not installed, run this once on macOS/Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
```

The short setup commands below assume `uv` is on PATH.

### Windows PowerShell — terminal conversation

Paste this block into PowerShell after installing `uv`. It installs the
package and starts BenchTrend. The first launch downloads and checks its data.
Choose the provider and model at the prompt, then enter a hidden API key.

```powershell
uv tool install 'benchtrend>=0.2.1'
if ($LASTEXITCODE -eq 0) { benchtrend }
```

### Install for Claude Code or Codex

These blocks assume `uv` and the selected AI client are already installed
and the client is signed in. Each installs BenchTrend, downloads its data
if missing, and opens a new client session. No BenchTrend API key is required.
Ask the client to use BenchTrend for benchmark questions.

For Codex:

```bash
uv tool install 'benchtrend>=0.2.1'
benchtrend codex
```

For Claude Code:

```bash
uv tool install 'benchtrend>=0.2.1'
benchtrend claude
```

On Windows, use `if ($LASTEXITCODE -eq 0) { benchtrend codex }` or
`if ($LASTEXITCODE -eq 0) { benchtrend claude }` after the install command.

### BenchTrend 0.2.0

Older versions require an explicit data download and separate registration.
This block works with the published 0.2.0 package:

```bash
uv tool install benchtrend==0.2.0 &&
benchtrend data install --url https://github.com/hyunyoungnam/BenchTrend/releases/download/data-20261007/benchtrend-data.tar.gz \
  --sha256 d19dcc2c47cc13738188176b5b87534e7e3eb0f0d11c86731bf41e0a1acca50e &&
benchtrend
```

For MCP on 0.2.0, run `benchtrend mcp --connect claude` or
`benchtrend mcp --connect codex`, then start that client.

## Install benchmark data

Use a separately distributed snapshot or BenchTrend data bundle:

```bash
# The published bundle (GitHub release data-20261007, 16 MB):
benchtrend data install
# Or select an explicit source:
benchtrend data install --url https://github.com/hyunyoungnam/BenchTrend/releases/download/data-20261007/benchtrend-data.tar.gz \
    --sha256 d19dcc2c47cc13738188176b5b87534e7e3eb0f0d11c86731bf41e0a1acca50e
# Or a local copy:
benchtrend data install --file ./benchtrend-data.tar.gz
benchtrend data status
```

`--file` also accepts the exported `benchmark_snapshot.json`. The bundle
manifest verifies its snapshot checksum; `--sha256` additionally verifies the
entire download. Installation validates schema and stages an atomic replacement.
Failed downloads, invalid snapshots and checksum failures retain existing data.
Updating data uses the same install command. Conversations retain their original
snapshot identity; continuing a conversation after a data update asks you to
start a new one or reinstall the original snapshot.

With no source arguments, `benchtrend data install` uses the dated release
and SHA-256 pinned in this code version. It does not follow GitHub's latest
release automatically. Explicit installs can replace existing data; automatic
first-launch downloads only happen when data is missing. A headless
`benchtrend ask` or stdio MCP request needs data installed beforehand.

On the collection machine, build and package reviewed data separately:

```bash
PYTHONPATH=src python3 -m bellwether benchmarks
PYTHONPATH=src python3 -m benchtrend data bundle --out dist/benchtrend-data.tar.gz
```

The bundle contains the self-contained benchmark snapshot and a manifest,
plus an adjacent `.sha256` file. No raw HTML, embeddings, browser assets or
Meilisearch database is required for either terminal conversations or MCP.
The corpus and generated bundles remain Git-ignored.

## Start a conversation

Choose OpenAI or Anthropic and supply an API key using the environment:

```bash
export OPENAI_API_KEY='YOUR_API_KEY'
benchtrend init --provider openai
benchtrend
```

For Anthropic, set `ANTHROPIC_API_KEY` and choose `--provider anthropic`.
`--model MODEL_ID` selects a model supported by your account; the compatibility
defaults are `gpt-6.1-sol` and `claude-opus-5-5`. `benchtrend init` stores the
provider, model and language, never the key. If no key is set, an interactive
session can prompt for a hidden key used only for that process. On Windows
PowerShell use `$env:OPENAI_API_KEY = 'YOUR_API_KEY'`.

Saved model preferences and resumed conversations retain their selected model.
To change a previously saved OpenAI default, run:

```bash
benchtrend init --provider openai --model gpt-6.1-sol
```

With no configuration or data, an interactive first launch downloads the
default data snapshot, then asks for provider/model and a session key if needed.

```text
$ benchtrend
> Which benchmarks are researchers using in robotics?
> Which newly introduced ones are used by other authors?
> Show the evidence for the first one.
```

Answers use the question's language by default; `--language ko` or `en` fixes
the prose language. Quotes preserve the authors' wording. Progress goes to
stderr, and final answers appear after quote and figure verification.
The prompt retains field, venue, period and role through the saved tool history.

Commands inside a conversation:

| Command | Action |
|---|---|
| `/sources` | Show the last answer's original quotes, paper links and verification marks |
| `/new` | Start a new conversation with the same model |
| `/chats` | List saved conversations |
| `/resume ID` | Continue a saved conversation |
| `/status` | Inspect installed data, API key presence, and whether Claude Code / Codex have the MCP server registered and connecting |
| `/help` | List conversation commands |
| `/exit` | Exit |

```bash
benchtrend chats
benchtrend --resume                 # newest saved conversation
benchtrend --resume CHAT_ID
benchtrend ask 'Which benchmarks are most used at ICML?' --json
benchtrend ask 'And which are new?' --resume CHAT_ID --json
```

Ctrl+C cancels a request without saving an unfinished turn. Quotes that fail
matching and figures absent from tool results are marked in the answer.
A matched quote or number does not prove the surrounding interpretation.

## Use within Codex or Claude Code

You can use the same installed package entirely through an existing AI client.
No OpenAI/Anthropic API key is needed by the BenchTrend MCP server; your client
manages its own model authentication.

```bash
benchtrend codex
# Or:
benchtrend claude
```

These shortcuts pass the BenchTrend MCP configuration to the new session.
They reuse installed data and your existing client login. Other configured
MCP servers remain available, and the shortcuts do not edit client settings.
Repeated launches therefore need no registration step. Add `--dry-run` to
print the launch command without downloading data or opening a session.
`benchtrend codex` selects `gpt-6.1-sol` by default. Use `--model MODEL_ID`
with either shortcut to choose a different client model for that session.

To register BenchTrend persistently so it also appears when launching the
client directly, the original commands remain available:

```bash
benchtrend mcp --connect codex
benchtrend mcp --connect claude
```

These commands register a `benchtrend` MCP entry using the client's official
CLI. Claude registration uses user scope; Codex uses its normal configuration
scope. Existing entries for other servers are retained. Use
`--dry-run` to inspect the exact registration command first. An existing entry
named `benchtrend` follows the client's own replacement/error behavior.
Restart the client session, then ask it to use BenchTrend for benchmark questions.

For other local MCP clients:

```bash
benchtrend mcp --config
```

This prints a configuration using an absolute Python executable and data
directory. The MCP host starts `benchtrend mcp` automatically as a local stdio
process. Running it manually waits for JSON-RPC requests; it is not a chat prompt.

The six exposed tools are `benchmark_scope`, `benchmark_usage`,
`benchmark_trend`, `new_benchmarks`, `benchmark_adoption`, and
`benchmark_evidence`. All are read-only and use the same invocation/validation
path as the independent terminal conversation. Server instructions explain
coverage, evaluation/training separation and evidence requirements.
External clients write their own final answers; BenchTrend's automatic final
answer verification runs in its own conversation interface.

Local data stays on your machine. Questions and returned tool evidence are sent
to your chosen model provider during inference. API authentication for the
independent terminal is separate from ChatGPT/Claude subscription login.

## Build a release

Code versions, data snapshots, and PyPI publication are explained in the
[release guide](releases.md).

```bash
python -m pip install build
python -m build
python -m pip install dist/benchtrend-0.2.1-py3-none-any.whl
```

The CI workflow tests Python 3.10 and 3.14, builds distribution artifacts,
installs the wheel into a fresh environment and checks an MCP conversation
from outside the source checkout. Publishing to PyPI and hosting a data bundle
are separate release steps; neither happens during a build.

Provider adapters follow the official [OpenAI function calling guide](https://developers.openai.com/api/docs/guides/function-calling)
and [Anthropic tool-call guide](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls).
The OpenAI default follows the [GPT-6.1 Sol model reference](https://developers.openai.com/api/docs/models/gpt-6.1-sol).
OpenAI requests use Responses with `store=false`, preserving reasoning items
between calls. Anthropic requests use Messages with paired tool-use/results.
Only BenchTrend's read-only tools are available to these API sessions.
