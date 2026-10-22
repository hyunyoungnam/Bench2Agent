# Bench2Agent releases

Code and research data are distributed separately. Version **0.6.1** is a
code-only update to 0.6.0: it makes the number checks stricter and clearer
and uses the same data release (data-20261021). Version 0.6.0 added the local
browser workspace (`bench2agent --web`), usage shares as percentages and the
links an introducing paper prints.

| Artifact | Contents |
|---|---|
| [PyPI 0.6.1](https://pypi.org/project/bench2agent/0.6.1/) | Standard-library terminal, browser workspace and stdio MCP; optional `server` dependencies |
| [GitHub v0.6.1](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/v0.6.1) | Identical wheel and source archive, checksums and release notes |
| [Data 2026-10-21](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/data-20261021) | SQLite database and manifest (data format v2), archive checksum and per-edition coverage CSV |

Install with `python -m pip install --upgrade bench2agent`, then `bench2agent`
(terminal) or `bench2agent --web` (browser). The official Claude Code or Codex
CLI must be installed and signed in separately. First launch downloads data
automatically. Both logins are supported; API keys are an explicit
alternative. See [the terminal guide](terminal.md) and
[the browser workspace](browser-interface.md).

## What changed in 0.6.1

- **Three number states, kept apart.** A number is *confirmed* when a tool
  value in the same context matches it. It is *differs from the tool result*
  when that context holds another value. It is *unverified* when the context
  cannot be fixed or no tool call supports it. A number restated without a
  tool call in its turn, and a date not found in the tool results, are
  unverified, never contradicted. Unverified numbers are counted, not left
  out. The browser shows each marked number's reason on hover and keyboard
  focus.
- **Same lookup, same scope.** A value pooled over editions connects only
  when the answer names exactly those editions; one edition's own value never
  stands in for a total. A ratio such as "82 of 513 papers (16.0%)" is judged
  as one claim: its count, denominator and share must come from the same
  lookup and the same denominator field. The installed data and one query's
  scope are kept apart. A stated measure ("listed", "analyzed", "matched")
  decides what a whole counts; without one, a total stays unverified.
  `benchmark_scope` also returns explicit sums over the editions it lists.
- **Saved answers.** Answers saved by 0.6.0 with "no tool call" numbers or
  unmatched dates now read as unverified in the terminal, the browser and its
  count of differing numbers. The saved files are not changed.
- **Browser example and docs.** A new example question on the start screen.
  The docs now say which code and data a Bench2Agent MCP server uses, and add
  a device checklist for macOS and native Windows (not yet checked).

0.6.1 uses data-20261021 unchanged; `bench2agent data install` is only needed
on a computer without that data.

## What changed in 0.6.0

- **Browser workspace.** `bench2agent --web [--port N] [--no-open]` starts a
  server on this computer only (loopback, a key minted per launch) and opens
  the page. It uses the signed-in CLI, the installed data and the same
  verifier as the terminal; a conversation started in one continues in the
  other. Questions keep running if the tab closes; a canceled question saves
  nothing. The evidence panel is built from the answer's own tool results:
  Benchmarks, Compare, Evidence and Data coverage. Conversations can be
  deleted from the list; they move to `data/bench2agent/chats/.deleted/` with
  their tool logs and can be restored from there.
- **Percentages.** Tools return `share_percent` (percent of the analyzed
  papers in the same scope, one decimal, `<0.1%` for small positive shares;
  unavailable, never 0%, without a denominator). Answers and the checker use
  percentages; `per_1000` remains for older clients and saved conversations.
- **Paper links.** `introducing_paper.links` lists links the introducing
  paper prints that were not confirmed as the benchmark's resource, each with
  what was observed at the address on a stated date. An address that could
  not be reached is not a finding that it was deleted or is private; a short
  file list is not a finding that no data exists.
- **Number checks.** A number is confirmed only against a tool value of the
  same context; table cells, file counts, shortened dates, comparisons such as
  "more than 4" and percentage points are read as such, and a tool sentence
  copied elsewhere is checked where it is placed.

## Research data

Data revision `1e1fa171722da693` has 61 editions of 11 conferences,
116,988 listed paper-edition observations, 88,873 matched to arXiv, 88,172
analyzed (full text processed) and 38,672 with subject labels, and 9,334
catalog entries, 8,770 with reviewed introduction claims. The release's
`coverage.csv` reports the listed, arXiv-matched, analyzed and labeled counts
per edition. These are observations, not unique-paper counts.

Against data-20261019 (`8503cf1b1f7bee99`) only links changed; papers, uses,
counts, adoption and introduction claims are the same:

- Benchmarks with a reviewed resource link: 89 → 315 (+226); reviewed link
  entries: 89 → 336 (+247). Most come from the "Code and Datasets — URL"
  lines introducing papers print in their front matter, each reviewed
  individually against the text at the link (an AI review, not human review);
  five are Hugging Face datasets with empty cards, accepted by their file lists.
- Paper links: 70 links not confirmed as a resource (79 benchmark
  connections, 74 benchmarks), each with what was seen there on 2026-10-21
  (UTC).

The bundle (data format v2) carries the database and its manifest; its
SHA-256 is
`f7c486a7302e1431bdbe721e3fb0ac9b163fd727c4984bb9716792dceacbc58f`.
It installs with Bench2Agent 0.5.0 or later; 0.5.0 serves it but does not show
the paper links or percentages. data-20261019 and the format-v1
data-20261013 still install from their explicit URLs.

## Upgrading

Code upgrades preserve installed data and chats, and never replace data
automatically:

1. Update the code with your installation method.
2. Run `bench2agent data install` to install and activate the data pinned
   in that code version.
3. Start a new conversation to use it; saved conversations keep their
   revision.
4. Restart an MCP client (Claude Code, Codex) so its server reads the
   active revision, or pass `--revision ID` explicitly.

Version 0.4.0 cannot install a data format v2 bundle; it keeps working with
data-20261013.

## Scope and limitations

The terminal, browser workspace and MCP are checked outside the checkout on
Linux/WSL; macOS uses the same POSIX implementation. Native Windows has not
been validated (cancellation, and the conversation lock, which uses fcntl). The device
checklist is in [platform-checks.md](platform-checks.md).
The browser workspace was checked with keyboard use and at desktop and narrow
widths. HTTP MCP and the earlier web host are optional development
prototypes; hosted sign-in, visitor isolation and public operation are not
completed. A number the checker cannot connect to a tool value is marked
unverified, which is not a finding that it is wrong. The checker reads a fixed
list of cues near each number, so some correct numbers stay unverified. A
confirmed number matches a tool value in context; this does not guarantee
that the surrounding sentence interprets it correctly.

## Release checklist

1. Freeze one data revision and verify it (installed, figures pinned in
   `scripts/regression_check.py`). Bundle its database and manifest, write
   its `coverage.csv` (`scripts/coverage_csv.py`) and checksum file. A
   rebuilt archive has other bytes: keep the file that is checksummed.
2. Set the version in `pyproject.toml` and `src/bench2agent/__init__.py`
   and the planned data URL and checksum in `src/bench2agent/data.py`;
   update README and this guide. Commit locally from a clean tree.
3. Run the Python and Node tests (also with the `server` extra and on
   Python 3.10), the regression figures and `twine check`.
4. Build the wheel and sdist from that commit. In a fresh environment outside
   the checkout, run `scripts/smoke_terminal.py`, install the bundle from a
   local URL with its checksum, ask through each connector, start once on an
   empty home, upgrade a home holding older data and resume a saved
   conversation.
5. With the owner's approval: publish the data release (bundle, checksum,
   coverage) and verify the default download with the same wheel.
6. Push, wait for CI, tag and publish the GitHub release with the same
   artifacts and checksums.
7. Publish the identical wheel and sdist to PyPI using a locally stored token.
8. Verify a fresh package-name installation, artifact hashes and data/tool
   queries.

If a check fails after step 2, fix it, rebuild and repeat the checks: the
files published are the files last verified.

Corpus exports, credentials, chats and raw paper archives remain Git-ignored.
Research bundles ship the snapshot's attributed evidence, not archived full texts.
