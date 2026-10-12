# Bench2Agent

Ask which benchmarks researchers evaluate on, how usage changes across conference
editions, and which newly introduced benchmarks other researchers adopt.
Answers carry computed counts, dataset coverage and original paper evidence.
Usage frequency describes adoption; it does not measure benchmark quality.

Python 3.10+ is required. These commands describe Bench2Agent 0.3.0, available on PyPI.
The runtime uses only Python's standard library;
no GPU, search engine, VS Code extension or extraction pipeline is needed.

## Install

For a copy-and-paste macOS/Linux installation that includes data and starts
the terminal conversation, use the [README quick start](../README.md#get-started).
For an existing AI client login, use the
[complete MCP setup](#install-for-claude-code-or-codex) below.

Install using **one** of the following methods. With Python 3.10+
in your environment:

```bash
python -m pip install bench2agent
bench2agent
```

Or, with [uv installed](#install-uv):

```bash
uv tool install bench2agent
bench2agent
```

`pip` installs in your active Python environment; a virtual environment is
recommended. `uv tool install` creates a separate environment for the app.
The first launch automatically downloads and checks the research-data snapshot.

If the browser installation (`install.sh`) is already on this machine, it has
put a `bench2agent` command on PATH, and `uv` will stop with "Executables
already exist". Add `--force`; the installed package provides the same
`bench2agent` command.

An installable wheel can be shared without the source checkout:

```bash
uv tool install ./bench2agent-0.3.0-py3-none-any.whl
```

The wheel includes code, not the research corpus. It runs from any working
directory. Installed runtime data normally lives in `~/.bench2agent`; a source
checkout uses its existing data, regardless of the checkout folder name.
Set `BENCH2AGENT_HOME` or pass
`--home DIR` to choose a different data directory. Code updates preserve data
and conversations.

Update with `uv tool upgrade bench2agent` or
`python -m pip install --upgrade bench2agent`, matching your installation method.

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
package and starts Bench2Agent. The first launch downloads and checks its data.
Choose the provider and model at the prompt, then enter a hidden API key.

```powershell
uv tool install bench2agent
if ($LASTEXITCODE -eq 0) { bench2agent }
```

### Install for Claude Code or Codex

These blocks assume `uv` and the selected AI client are already installed
and the client is signed in. Each installs Bench2Agent, downloads its data
if missing, and opens a new client session. No Bench2Agent API key is required.
Ask the client to use Bench2Agent for benchmark questions.

For Codex:

```bash
uv tool install bench2agent
bench2agent codex
```

For Claude Code:

```bash
uv tool install bench2agent
bench2agent claude
```

On Windows, use `if ($LASTEXITCODE -eq 0) { bench2agent codex }` or
`if ($LASTEXITCODE -eq 0) { bench2agent claude }` after the install command.

## Switch from an earlier installation

The former `benchtrend` PyPI package remains available, but its updates do not
automatically install the renamed application. Install `bench2agent` using the
pip or uv method above. Run `benchtrend status` to find the previous data home,
then copy its snapshot, model settings and saved conversations:

```bash
bench2agent migrate --from /path/to/previous/data-home
bench2agent data status
bench2agent chats
```

Typical previous homes are `~/.benchtrend` or `~/.bellwether`. The migration
leaves the original files intact and skips existing destination files. It
preserves conversation IDs and their snapshot identities, and does not copy
API keys, raw papers or the full browser corpus. A source checkout keeps its
existing folder name; the same command can copy its earlier chat directory.

Reconnect an existing MCP registration under the new server name:

```bash
bench2agent mcp --connect claude
# Or, for Codex:
bench2agent mcp --connect codex
```

Restart the client afterward. The old `benchtrend` MCP registration can be
removed with the client's `mcp remove benchtrend` command once the new server
works. If the older tool was installed with uv, `uv tool uninstall benchtrend`
removes its old commands after migration; with pip, use
`python -m pip uninstall benchtrend`. Neither command removes the saved data.

## Install benchmark data

Use a separately distributed snapshot or Bench2Agent data bundle:

```bash
# The published bundle (GitHub release data-20261007, 16 MB):
bench2agent data install
# Or select an explicit source:
bench2agent data install --url https://github.com/hyunyoungnam/Bench2Agent/releases/download/data-20261007/bench2agent-data.tar.gz \
    --sha256 d19dcc2c47cc13738188176b5b87534e7e3eb0f0d11c86731bf41e0a1acca50e
# Or a local copy:
bench2agent data install --file ./bench2agent-data.tar.gz
bench2agent data status
```

Every revision holds the snapshot JSON and its SQLite import. Queries read
the JSON unless `BENCH2AGENT_STORE=sqlite` (or `auto`, which uses the SQLite
file only when it was built from the installed JSON; an explicit `sqlite`
with a stale or unreadable file reports why instead of answering). Each tool
call reads one version of the file. A `.sqlite` file installs on its own
with `data install --file`, and `data bundle` includes the database. Both
backends return the same results and are checked against each other in the
test suite and by `scripts/parity_check.py`.

### Data revisions

Every install is a revision. The snapshot and its SQLite import are staged
together under `data/revisions/`, validated and checked against each other,
moved into `data/revisions/<snapshot_id>/` with one rename, and only then
named in `data/revisions/active`. A failed install, at any step, leaves the
active revision serving with both of its files unchanged. Published
revisions are never edited.

- **Pinning.** Everything reads an explicit revision. A terminal question
  fixes the revision when it starts (a saved conversation keeps the one its
  first answer was given on) and uses it for every tool call, figure
  recomputation and quote check in that turn. The local stdio MCP server
  (`bench2agent mcp`) reads the revision that was active when it started,
  for its whole life, and says so in its instructions and in every response
  (`revision`). Restart the server to serve the currently active revision;
  `bench2agent mcp --revision <snapshot_id>` serves an installed earlier
  revision to reproduce earlier answers.
- **Switching.** `bench2agent data activate <snapshot_id>` verifies that
  revision (manifest, files, checksums, JSON and database from the same
  snapshot) and makes it the one new conversations and new servers get.
  A rollback is the same command. `data status` lists revisions, the pointer
  state and the served revision's verification.
- **No fallback.** With no `active` pointer nothing is served and the
  commands say to install; a pointer whose target is missing or damaged is
  an error naming the recovery (`data activate` an installed revision, or
  reinstall a verified bundle). Data is never read from any other place.
- **Same content, same revision.** `snapshot_id` identifies the extraction
  inputs (it is derived from their file stamps). The manifest also records
  `content_sha256`, the content without `generated_at`, so re-exporting the
  same extraction and installing it again is recognised and the installed
  copy is kept. Same id with different content is refused.
- **Repair is a restore.** A damaged copy is repaired only from bytes that
  match the manifest's recorded checksums (reinstall the original bundle);
  the manifest is never rewritten, and a database is never rebuilt inside a
  revision. Databases built locally from a JSON-only source are marked
  `sqlite_built_locally` with the SQLite library version, since the bytes of
  an import differ between SQLite builds even for identical content.
  Official bundles therefore carry the database.
- **Previous installs.** A `data/processed/benchmark_snapshot.json` from
  before revisions is registered once, explicitly, with
  `bench2agent data install --file data/processed/benchmark_snapshot.json`;
  `bench2agent migrate --from DIR` does this for a previous installation.
  The source checkout registers a build the same way, or with
  `bench2agent benchmarks --publish`, which builds the snapshot from the
  reviewed extraction and installs it as the new active revision in one
  step. Adding a conference edition is therefore the per-edition `icml`
  stages (collect, normalize, arXiv match, HTML parse, `bench_mentions
  --all`) followed by publish.

`bench2agent data sqlite SNAPSHOT.json --out FILE.sqlite` builds a database
for bundling; it refuses to write inside `data/revisions/`.

`--file` also accepts the exported `benchmark_snapshot.json` or a
`benchmark_snapshot.sqlite`. The bundle
manifest verifies its snapshot checksum; `--sha256` additionally verifies the
entire download. Installation validates schema and stages an atomic replacement.
Failed downloads, invalid snapshots and checksum failures retain existing data.
Updating data uses the same install command. Conversations retain their original
snapshot identity; continuing a conversation after a data update asks you to
start a new one or reinstall the original snapshot.

With no source arguments, `bench2agent data install` uses the dated release
and SHA-256 pinned in this code version. It does not follow GitHub's latest
release automatically. Explicit installs can replace existing data; automatic
first-launch downloads only happen when data is missing. A headless
`bench2agent ask` or stdio MCP request needs data installed beforehand.

On the collection machine, build and package reviewed data separately:

```bash
PYTHONPATH=src python3 -m bench2agent benchmarks
PYTHONPATH=src python3 -m bench2agent data bundle --out dist/bench2agent-data.tar.gz
```

The bundle contains the self-contained benchmark snapshot and a manifest,
plus an adjacent `.sha256` file. No raw HTML, embeddings, browser assets or
Meilisearch database is required for either terminal conversations or MCP.
The corpus and generated bundles remain Git-ignored.

## Start a conversation

The terminal uses the same execution layer as the web. `--connector claude`
or `--connector codex` runs the question through the official CLI with its
own login (Bench2Agent gives it the pinned MCP server and records the calls);
the default `--connector api` uses a provider API key as below.
`bench2agent claude` / `bench2agent codex` are different: they open the
official CLI itself with Bench2Agent's tools registered, and Bench2Agent does
not verify those answers.

Choose OpenAI or Anthropic and supply an API key using the environment:

```bash
export OPENAI_API_KEY='YOUR_API_KEY'
bench2agent init --provider openai
bench2agent
```

For Anthropic, set `ANTHROPIC_API_KEY` and choose `--provider anthropic`.
`--model MODEL_ID` selects a model supported by your account; the compatibility
defaults are `gpt-6.1-sol` and `claude-opus-5-5`. `bench2agent init` stores the
provider, model and language, never the key. If no key is set, an interactive
session can prompt for a hidden key used only for that process. On Windows
PowerShell use `$env:OPENAI_API_KEY = 'YOUR_API_KEY'`.

Saved model preferences and resumed conversations retain their selected model.
To change a previously saved OpenAI default, run:

```bash
bench2agent init --provider openai --model gpt-6.1-sol
```

With no configuration or data, an interactive first launch downloads the
default data snapshot, then asks for provider/model and a session key if needed.

```text
$ bench2agent
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
bench2agent chats
bench2agent --resume                 # newest saved conversation
bench2agent --resume CHAT_ID
bench2agent ask 'Which benchmarks are most used at ICML?' --json
bench2agent ask 'And which are new?' --resume CHAT_ID --json
```

Ctrl+C cancels a request without saving an unfinished turn. Quotes that fail
matching and figures absent from tool results are marked in the answer.
A matched quote or number does not prove the surrounding interpretation.

## Use within Codex or Claude Code

You can use the same installed package entirely through an existing AI client.
No OpenAI/Anthropic API key is needed by the Bench2Agent MCP server; your client
manages its own model authentication.

```bash
bench2agent codex
# Or:
bench2agent claude
```

These shortcuts pass the Bench2Agent MCP configuration to the new session.
They reuse installed data and your existing client login. Other configured
MCP servers remain available, and the shortcuts do not edit client settings.
Repeated launches therefore need no registration step. Add `--dry-run` to
print the launch command without downloading data or opening a session.
`bench2agent codex` selects `gpt-6.1-sol` by default. Use `--model MODEL_ID`
with either shortcut to choose a different client model for that session.

To register Bench2Agent persistently so it also appears when launching the
client directly, the original commands remain available:

```bash
bench2agent mcp --connect codex
bench2agent mcp --connect claude
```

These commands register a `bench2agent` MCP entry using the client's official
CLI. Claude registration uses user scope; Codex uses its normal configuration
scope. Existing entries for other servers are retained. Use
`--dry-run` to inspect the exact registration command first. An existing entry
named `bench2agent` follows the client's own replacement/error behavior.
Restart the client session, then ask it to use Bench2Agent for benchmark questions.

For other local MCP clients:

```bash
bench2agent mcp --config
```

This prints a configuration using an absolute Python executable and data
directory. The MCP host starts `bench2agent mcp` automatically as a local stdio
process. Running it manually waits for JSON-RPC requests; it is not a chat prompt.

### Streamable HTTP

With the server extra (`pip install 'bench2agent[server]'`, the official MCP
Python SDK), `bench2agent mcp --http [--host 127.0.0.1] [--port 8765]`
serves the same six tools over Streamable HTTP. The data revision is fixed by
the path and by nothing else: `/revisions/<snapshot_id>/mcp` serves that
installed revision for every call, whatever is active, which is how a hosted
conversation keeps its revision; `/mcp` serves the revision the process
started on (`--revision` names one), like the stdio server; `/revisions`
lists what is installed. Every response carries `revision`, requests are
stateless, and a damaged or removed revision answers with its recovery
message, never with other data.

The six exposed tools are `benchmark_scope`, `benchmark_usage`,
`benchmark_trend`, `new_benchmarks`, `benchmark_adoption`, and
`benchmark_evidence`. All are read-only and use the same invocation/validation
path as the independent terminal conversation. Each benchmark row carries two
separate links: `locations`, our mapping from the benchmark id to a Hugging
Face dataset, GitHub repository or homepage (`check` says whether the id
answered the API at the registry's last check; `checked_at` is null when the
installed snapshot does not record that date; an empty list means no
confirmed location, not that none exists), and `introducing_paper`, the
reviewed introducing paper in this corpus, or null. A homonym has its own
links. Nothing ranks or filters on either. Server instructions explain
coverage, evaluation/training separation and evidence requirements.
External clients write their own final answers; Bench2Agent's automatic final
answer verification runs in its own conversation interface.

Local data stays on your machine. Questions and returned tool evidence are sent
to your chosen model provider during inference. API authentication for the
independent terminal is separate from ChatGPT/Claude subscription login.

## Hosted conversation (web)

This is the way a user is meant to reach Bench2Agent: open an address,
connect an AI account they already have, ask, read the sources and the
verification marks. No Bench2Agent account, no API key by default. On this
machine (loopback) the page uses the Claude Code or Codex login already here;
[account-connections.md](account-connections.md) records what each route
permits and what a public host can offer today. The sections below are for
the operator who runs the host.

With the server extra, `bench2agent web serve [--host 127.0.0.1] [--port 8780]`
runs the host: a page with a question box, conversation history and
expandable verified evidence, over a JSON API. It is the same model loop and
the same verifier as the terminal; its tool calls travel over HTTP MCP to the
server it mounts at `/mcp-server`, on the endpoint of the conversation's
revision. The `Dockerfile` builds the same host as one container with a
`/data` volume.

- **Connections, not sign-up.** The page lists the connectors the host can
  offer (`GET /api/connectors`): the official Claude Code and Codex CLIs with
  their own logins (on a loopback host, the machine's logins; on a hosted
  deployment they show as unavailable, since each visitor would need an
  isolated login, which is not built), and a provider API key. A
  conversation is created on one connector and keeps it. CLI connectors
  receive Bench2Agent's stdio MCP server pinned to the conversation's
  revision; the server records every call to a per-attempt log that becomes
  the turn's trail.
- **Visitors and ownership.** On a loopback host access is open: the first
  visit opens a session (an HttpOnly, SameSite=Lax cookie) with no name or
  code. On any other host access defaults to invite codes
  (`BENCH2AGENT_WEB_ACCESS=open|invite`; `bench2agent web invite add LABEL`,
  `invite list`, `invite revoke`). State-changing requests from a browser
  session must carry `X-Requested-With: Bench2Agent`, which the page adds and
  a cross-site form cannot. A conversation exists only for its owner: read,
  ask, cancel and delete on someone else's return 404. Scripts may
  authenticate with a bearer token from `bench2agent web user add NAME`
  (`user token`, `user revoke`, `user list`); that is an operator path.
- **Revision per conversation.** A new conversation is pinned to the revision
  active at that moment and keeps it for its life: tool calls, figure
  recomputation and quote checks all read that revision after a new one is
  published; a removed revision gives recovery guidance, never another
  revision's answer. `GET /api/revisions` shows what is installed.
- **Model credentials are deployment configuration**
  (`BENCH2AGENT_WEB_CREDENTIALS`). *Service mode*: the operator configures a
  provider key on the server, the host uses it for every user's questions
  under the per-user limits below, and no user ever receives that key. *User
  mode*: each question carries the user's own provider key in `X-Model-Key`;
  the key travels from the browser through the Bench2Agent backend to the
  model provider for that request and is neither stored nor logged. Unset,
  the mode is service when the host environment holds a provider key, else
  user; the page shows which. The operator's Claude Code or Codex login is
  never used for web users.
- **Limits and cancellation.** One question at a time per conversation, at
  most two per user, questions up to 4,000 characters. The daily cap
  (`BENCH2AGENT_WEB_TURNS_PER_DAY`, default 100) counts *attempts*, reserved
  in one database transaction before the model is called, so a cancelled or
  failed question counts and two requests with one attempt left cannot both
  pass. `POST /api/conversations/{id}/cancel` is accepted only while the
  question is still running (model loop or verification); once saving has
  started it answers `cancelled: false` with "too late", and the answer is
  saved. An accepted cancel is never followed by a save. The running slot
  belongs to the job, not to the browser connection: a dropped connection
  releases nothing until the job ends. A failed or cancelled question saves
  nothing and echoes no credential.
- **Streaming.** `POST /api/conversations/{id}/ask` answers as NDJSON: tool
  events as they happen, then the verified turn (or an error). Each saved
  turn carries `timing` (attempt id; total, model, tool and verification
  milliseconds; tool calls), and `bench2agent web attempts [--user NAME]`
  lists attempts with the same split for review beside the saved
  conversation. In the page a question belongs to the conversation it was
  asked in: switching to another conversation while it runs shows nothing of
  it there, cancel targets the asking conversation, and the finished answer
  is in its own conversation when reopened.
- **Internal MCP.** The host mounts its HTTP MCP server at `/mcp-server` and
  reaches it over HTTP with an internal bearer token generated at startup and
  sent on every internal request; the mount accepts nothing else, and the
  token is never logged or shown. `BENCH2AGENT_MCP_URL` and
  `BENCH2AGENT_MCP_TOKEN` point the host at an external HTTP MCP instead.
- **Links in answers.** The page renders the model's prose as text, turning
  only `[text](https://…)` links and bare http(s) addresses into anchors, with
  text and address escaped separately; no other markup is interpreted.

The host's own state (users, tokens as hashes, conversations, attempts) lives
in `data/bench2agent/web.sqlite`, apart from the research revisions. It binds
to loopback by default; a public deployment needs HTTPS termination in front
and is not part of this release. [web-live-check.md](web-live-check.md) is
the procedure for checking real answers from the page.

## Build a release

For development, install from a local checkout with `uv tool install .` or
`python -m pip install .`. To install development code directly from GitHub:

```bash
python -m pip install https://github.com/hyunyoungnam/Bench2Agent/archive/refs/heads/main.tar.gz
```

Code versions, data snapshots, and PyPI publication are explained in the
[release guide](releases.md).

```bash
python -m pip install build
python -m build
python -m pip install dist/bench2agent-0.3.0-py3-none-any.whl
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
Only Bench2Agent's read-only tools are available to these API sessions.
