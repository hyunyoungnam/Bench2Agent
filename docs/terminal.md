# BenchTrend

Ask which benchmarks researchers evaluate on, how usage changes across conference
editions, and which newly introduced benchmarks other researchers adopt.
Answers carry computed counts, dataset coverage and original paper evidence.
Usage frequency describes adoption; it does not measure benchmark quality.

Python 3.10+ is required. The runtime uses only Python's standard library;
no GPU, search engine, VS Code extension or extraction pipeline is needed.

## Install

From a checkout:

```bash
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
uv tool install ./benchtrend-0.2.0-py3-none-any.whl
```

The wheel includes code, not the research corpus. It runs from any working
directory. Installed runtime data normally lives in `~/.benchtrend`; a source
checkout uses its existing data. An existing `~/.bellwether` snapshot is reused
when there is no new installation directory. Set `BENCHTREND_HOME` or pass
`--home DIR` to choose a different data directory. Code updates preserve data
and conversations.

This version is prepared for package distribution. `uv tool install benchtrend`
and `pip install benchtrend` should be advertised only after publishing this
package to PyPI; these commands are not a promise that a public release exists.

## Install benchmark data

Use a separately distributed snapshot or BenchTrend data bundle:

```bash
benchtrend data install --file ./benchtrend-data.tar.gz
# Or a publisher-provided public HTTPS URL:
benchtrend data install --url https://YOUR_HOST/benchtrend-data.tar.gz --sha256 EXPECTED_SHA256
benchtrend data status
```

`--file` also accepts the exported `benchmark_snapshot.json`. The bundle
manifest verifies its snapshot checksum; `--sha256` additionally verifies the
entire download. Installation validates schema and stages an atomic replacement.
Failed downloads, invalid snapshots and checksum failures retain existing data.
Updating data uses the same install command. Conversations retain their original
snapshot identity; continuing a conversation after a data update asks you to
start a new one or reinstall the original snapshot.

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
defaults are `gpt-5-mini` and `claude-opus-5-5`. `benchtrend init` stores the
provider, model and language, never the key. If no key is set, an interactive
session can prompt for a hidden key used only for that process. On Windows
PowerShell use `$env:OPENAI_API_KEY = 'YOUR_API_KEY'`.

With no configuration or data, an interactive first launch asks for a local
data file or HTTPS URL, then provider/model and a session key if needed.

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
| `/status` | Inspect installed data and API key presence |
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
benchtrend mcp --connect codex
# Or:
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

```bash
python -m pip install build
python -m build
python -m pip install dist/benchtrend-0.2.0-py3-none-any.whl
```

The CI workflow tests Python 3.10 and 3.14, builds distribution artifacts,
installs the wheel into a fresh environment and checks an MCP conversation
from outside the source checkout. Publishing to PyPI and hosting a data bundle
are separate release steps; neither happens during a build.

Provider adapters follow the official [OpenAI function calling guide](https://developers.openai.com/api/docs/guides/function-calling)
and [Anthropic tool-call guide](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls).
OpenAI requests use Responses with `store=false`, preserving reasoning items
between calls. Anthropic requests use Messages with paired tool-use/results.
Only BenchTrend's read-only tools are available to these API sessions.
