# Bench2Agent

Ask which benchmarks researchers evaluate on, how usage changes across conference
editions, and which newly introduced benchmarks other researchers adopt.
Answers carry computed counts, dataset coverage and original paper evidence.
Usage frequency describes adoption; it does not measure benchmark quality.

Python 3.10+ is required. Install and sign in to the official Claude Code or Codex CLI separately before starting the conversation. These commands describe Bench2Agent 0.5.0, available on PyPI.
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
python -m pip install --upgrade bench2agent
bench2agent
```

Or, with [uv installed](#install-uv):

```bash
uv tool install --upgrade bench2agent
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
uv tool install ./bench2agent-0.5.0-py3-none-any.whl
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
A signed-in Claude Code or Codex is selected; when both are present, choose one. Native Windows cancellation is unvalidated; WSL is the tested Windows path.

```powershell
uv tool install --upgrade bench2agent
if ($LASTEXITCODE -eq 0) { bench2agent }
```

### Install for Claude Code or Codex

These blocks assume `uv` and the selected AI client are already installed
and the client is signed in. Each installs Bench2Agent, downloads its data
if missing, and opens a new client session. No Bench2Agent API key is required.
Ask the client to use Bench2Agent for benchmark questions.

For Codex:

```bash
uv tool install --upgrade bench2agent
bench2agent codex
```

For Claude Code:

```bash
uv tool install --upgrade bench2agent
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
# The published bundle (GitHub release data-20261019, about 45 MB):
bench2agent data install
# Or select an explicit source, e.g. the earlier data-20261013 for reproduction:
bench2agent data install --url https://github.com/hyunyoungnam/Bench2Agent/releases/download/data-20261013/bench2agent-data.tar.gz \
    --sha256 9195fce6afd7e4ae7448be67bd9a6f17e5374bbbbe602f3900737088f69d3af2
# Or a local copy:
bench2agent data install --file ./bench2agent-data.tar.gz
bench2agent data status
```

Installed data is never replaced automatically, and a code upgrade does not
download new data. After upgrading the code, run `bench2agent data install` to
install and activate the new release. Saved conversations keep the revision
they started on, so start a new conversation (`/new`) to use the new data. An
MCP client (Claude Code, Codex) keeps the revision its Bench2Agent server
started with: restart the client to read the active revision. Bench2Agent
0.4.0 cannot install a data format v2 bundle such as data-20261019.

A revision is one SQLite database (`benchmark_snapshot.sqlite`) and its
manifest. Queries read that file and nothing else; each tool call reads one
version of it. A `.sqlite` written by `bench2agent benchmarks` installs on its
own with `data install --file`, and `data bundle` packs the database and the
manifest (data format v2). Bundles of data format v1 (JSON + database, as
released up to data-20261013) and bare legacy `benchmark_snapshot.json`
files still install: the JSON is converted once into a database and kept
beside it, listed and checked like any file, never queried.

### Data revisions

Every install is a revision. The database (and a legacy JSON, when the
source carried one) is staged under `data/revisions/`, validated, moved into
`data/revisions/<revision_id>/` with one rename, and only then named in
`data/revisions/active`. A failed install, at any step, leaves the active
revision serving with its files unchanged. Published revisions are never
edited.

- **Pinning.** Everything reads an explicit revision. A terminal question
  fixes the revision when it starts (a saved conversation keeps the one its
  first answer was given on) and uses it for every tool call, figure
  recomputation and quote check in that turn. The local stdio MCP server
  (`bench2agent mcp`) reads the revision that was active when it started,
  for its whole life, and says so in its instructions and in every response
  (`revision`). Restart the server to serve the currently active revision;
  `bench2agent mcp --revision <revision_id>` serves an installed earlier
  revision to reproduce earlier answers.
- **Switching.** `bench2agent data activate <revision_id>` verifies that
  revision (manifest, every listed file's checksum, the database's identity,
  schema and minimum client version) and makes it the one new conversations
  and new servers get. A rollback is the same command. `data status` lists
  revisions, the pointer state and the served revision's verification.
- **No fallback.** With no `active` pointer nothing is served and the
  commands say to install; a pointer whose target is missing or damaged is
  an error naming the recovery (`data activate` an installed revision, or
  reinstall a verified bundle). Data is never read from any other place.
- **The id is the content.** A revision built by `bench2agent benchmarks`
  is named by a hash of its research content (`content_sha256`, the first
  16 hex digits are the id); the same extraction and review built again, on
  another day or machine, is the same revision, and installing it again is
  recognised and the installed copy kept. Same id with different content is
  refused. Revisions installed from legacy JSON keep that snapshot's own id.
  Content hashes carry a rule name: `v1-json` is what 0.4.0 manifests
  recorded, `v2-logical` the current id. Every hash a bundle, a database or
  a legacy JSON offers is recomputed from the installed database's content
  under its own rule and must match; hashes compare only under the same
  rule; an unknown rule is refused; the installed manifest records what was
  computed (`content_hashes`) and is never rewritten afterwards.
- **Readable by.** The manifest records `db_schema_version` and
  `min_client_version`; older code refuses a revision it cannot read, with
  the version it needs, instead of misreading it. Revisions installed by
  0.4.0 (manifest format v1) are still served by newer code.
- **Repair is a restore.** A damaged file is repaired only from bytes that
  match the manifest's recorded checksum (reinstall the original bundle);
  the manifest is never rewritten, and a database is never rebuilt inside a
  revision. Databases converted locally from a JSON-only source are marked
  `sqlite_built_locally` with the SQLite library version, since the bytes of
  a conversion differ between SQLite builds even for identical content.
  Official bundles therefore carry the database.
- **Previous installs.** A `data/processed/benchmark_snapshot.json` from
  before revisions is registered once, explicitly, with
  `bench2agent data install --file data/processed/benchmark_snapshot.json`;
  `bench2agent migrate --from DIR` does this for a previous installation.
  The source checkout registers a build with `bench2agent benchmarks
  --publish`, which builds the database from the reviewed extraction and
  installs it as the new active revision in one step. Adding a conference
  edition is therefore the per-edition `icml` stages (collect, normalize,
  arXiv match, HTML parse, `bench_mentions`) followed by publish.

`bench2agent data sqlite SNAPSHOT.json --out FILE.sqlite` converts a legacy
JSON snapshot into a database file; it refuses to write inside
`data/revisions/`.

`--file` accepts a bundle, a `benchmark_snapshot.sqlite` or a legacy
`benchmark_snapshot.json`. The bundle manifest verifies each file's
checksum; `--sha256` additionally verifies the entire download. Installation validates schema and stages an atomic replacement.
Failed downloads, invalid snapshots and checksum failures retain existing data.
Updating data uses the same install command. Conversations retain their original
snapshot identity; continuing a conversation after a data update uses its original installed revision. If that revision was removed, reinstall it or start a new conversation.

With no source arguments, `bench2agent data install` uses the dated release
and SHA-256 pinned in this code version. It does not follow GitHub's latest
release automatically. Explicit installs can replace existing data; automatic
first-launch downloads only happen when data is missing. A headless
`bench2agent ask` or stdio MCP request needs data installed beforehand.

On the collection machine, build and package reviewed data separately:

```bash
PYTHONPATH=src python3 -m icml.update status          # what each edition still needs
PYTHONPATH=src python3 -m icml.update run --publish   # fetch, parse, extract and publish what changed
PYTHONPATH=src python3 -m bench2agent data bundle --out dist/bench2agent-data.tar.gz
```

`icml.update` keeps a working database of what was done with which code
(`data/work/`), re-does only what changed, queues new or changed
introduction claims for review, and takes one lock; `--if-due 7` makes a
daily OS timer run it weekly and catch a missed week up once.
`bench2agent benchmarks --publish` alone builds and installs from whatever
the pipeline files hold.

The bundle contains the revision's database and a manifest, plus an
adjacent `.sha256` file. No raw HTML, embeddings, browser assets or
Meilisearch database is required for either terminal conversations or MCP.
The corpus and generated bundles remain Git-ignored.

## Start a conversation

The terminal is the local side and uses the same execution layer as the
web. Bench2Agent's install is separate from the official CLIs: install
Claude Code or Codex and sign in with its own login first. Then an
interactive `bench2agent` finds the signed-in CLI and answers with it
(asking which when both are), giving it the pinned MCP server with built-in
Claude built-in tools disabled (Codex runs in its read-only sandbox) and recording every call; `--connector claude|codex` names
one, and `bench2agent status` lists them. Scripts (`bench2agent ask`) take
`--connector` or the saved choice, else the API key. `bench2agent claude` /
`bench2agent codex` are different: they open the official CLI itself with
Bench2Agent's tools registered, and Bench2Agent does not verify those
answers.

The API key is the explicit alternative (`--connector api`): choose OpenAI
or Anthropic and supply the key through the environment:

```bash
export OPENAI_API_KEY='YOUR_API_KEY'
bench2agent init --provider openai
bench2agent --connector api
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

## Browser interface direction (planned)

The two tracks are terminal and browser interfaces over one local application,
with terminal implementation first. The browser workspace runs on your PC,
uses the same installed Claude Code or Codex login, and presents benchmark
cards, comparisons and source evidence alongside the conversation. It needs
no dedicated paid backend or Bench2Agent account, but does require local
installation and a running process. See [browser-interface.md](browser-interface.md).

Since 0.6.0, `bench2agent --web [--port N] [--no-open]` starts it
([current state](browser-interface.md#current-state-version-060-2026-10-21)).
Conversations are shared: `/resume ID` continues one started in the browser,
and the browser shows conversations started here. The commands in the
following section start the earlier hosted visitor prototype, not this local
workspace. Public multi-user hosting is deferred.

## Earlier hosted conversation prototype (deferred)

The hosted prototype separates visitors by runtime directory and environment
under one OS user, not by an OS-user or container
boundary. The web never uses the host's own
logins. [account-connections.md](account-connections.md) records what each
route permits, what was executed, and what remains before public operation.
The sections below are for the operator who runs the host.

With the server extra, `bench2agent web serve [--host 127.0.0.1] [--port 8780]`
runs the host: a page with a question box, conversation history and
expandable verified evidence, over a JSON API. It is the same model loop and
the same verifier as the terminal; its tool calls travel over HTTP MCP to the
server it mounts at `/mcp-server`, on the endpoint of the conversation's
revision. The `Dockerfile` builds the same host as one container with a
`/data` volume.

- **Connect, then ask.** The page shows one connector: *Claude Code (your
  Claude login)*. Connecting runs the official `claude auth login` in a
  runtime of the visitor's own on the host and relays its terminal to the
  visitor (and the visitor's input back); the host records only the shape
  of the exchange. A visitor who has not connected can read but not ask, and
  the connection is re-checked at every question. *Disconnect* stops any
  running question, runs the official logout and removes the runtime.
  Conversations keep their connector and the CLI's own conversation id; the
  CLI gets Bench2Agent's stdio MCP server pinned to the conversation's
  revision, with built-in tools disabled, and the server records every call
  to a per-attempt log that becomes the turn's trail. A turn whose log is
  missing, damaged, incomplete or from another revision is saved as
  unverifiable, shown as plain text without marks. After a cancel, a failure,
  an unverifiable turn or a save conflict the CLI session is not resumed; the
  next question carries only the saved turns.
- **Visitors and ownership.** The first visit opens a session (an HttpOnly,
  SameSite=Lax cookie) with no name, code or password; it only keeps
  conversations apart and owned. State-changing requests from a browser
  session must carry `X-Requested-With: Bench2Agent`, which the page adds and
  a cross-site form cannot. A conversation exists only for its owner: read,
  ask, cancel and delete on someone else's return 404. Scripts may
  authenticate with a bearer token from `bench2agent web user add NAME`; that
  is an operator path. Runtime limits: `BENCH2AGENT_WEB_MAX_RUNTIMES` (50),
  `BENCH2AGENT_WEB_MAX_PROCESSES` (4), `BENCH2AGENT_WEB_IDLE_DAYS` (30).
- **The API-key connector** is an operator option for checking a host
  without a Claude login (`BENCH2AGENT_WEB_API_CONNECTOR=1`); it is not part
  of the visitor's path.
- **Revision per conversation.** A new conversation is pinned to the revision
  active at that moment and keeps it for its life: tool calls, figure
  recomputation and quote checks all read that revision after a new one is
  published; a removed revision gives recovery guidance, never another
  revision's answer. `GET /api/revisions` shows what is installed.
- **Model credentials for the API-key option** (`BENCH2AGENT_WEB_CREDENTIALS`):
  *service*, the operator's key on the server, used under the per-user
  limits and never sent to a user; *user*, the user's own key in
  `X-Model-Key`, travelling browser -> backend -> provider for that request
  only, neither stored nor logged. The operator's Claude Code or Codex login
  is never used for web users.
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
and is not validated for public operation. The optional code is packaged as a prototype, not a hosted service. [web-live-check.md](web-live-check.md) is
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
python -m pip install dist/bench2agent-0.5.0-py3-none-any.whl
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
