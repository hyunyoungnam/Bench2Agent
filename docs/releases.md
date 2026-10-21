# Bench2Agent releases

Code and research data are distributed separately. Version **0.5.0** reads
only SQLite data revisions, identifies papers by their official ids and the
feeds' own links, records the origin of every field label, attaches
individually reviewed code and data locations, and checks each printed number
against a tool value of the same context in the turn's own tool log.

| Artifact | Contents |
|---|---|
| [PyPI 0.5.0](https://pypi.org/project/bench2agent/0.5.0/) | Standard-library terminal and stdio MCP; optional `server` dependencies |
| [GitHub v0.5.0](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/v0.5.0) | Identical wheel and source archive, checksums and release notes |
| [Data 2026-10-19](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/data-20261019) | SQLite database and manifest (data format v2), archive checksum and per-edition coverage CSV |

Install with `python -m pip install --upgrade bench2agent`, then `bench2agent`.
The official Claude Code or Codex CLI must be installed and signed in separately.
First interactive launch downloads data automatically. Both logins are supported;
API keys are an explicit alternative. See [the terminal guide](terminal.md).

## Research data

Data revision `8503cf1b1f7bee99` has 61 editions of 11 conferences,
116,988 listed paper-edition observations, 88,873 matched to arXiv, 88,172
parsed and 38,672 with field labels, and 9,334 catalog entries: 8,770 with
reviewed introduction claims and 89 with an individually reviewed code or
data location. The release's `coverage.csv` reports the listed, arXiv-matched,
parsed and labeled counts per edition. These are observations, not
unique-paper counts.

Against data-20261013 (`e41da62f2fb3616f`), paper identity now merges rows
only on official ids or the feeds' own links (titles only propose
candidates), proceedings volume ids are no longer counted as fields, the
arXiv index was re-harvested and re-matched, six reviewed match decisions
link papers the automatic matcher missed, and reviewed locations state
what was seen about the data, where and when. Differences between the two
revisions for the same edition come from these data and rule changes; they
are not usage trends, which compare editions inside one revision.

The bundle (data format v2) carries the database and its manifest; its
SHA-256 is
`ff4c39a2555e868b71d8feae5147d4e2aab85dc7d77467172e7a3b33b6be3aad`.
It needs Bench2Agent 0.5.0 or later. The earlier data-20261013 bundle
(format v1, JSON + database) still installs from its explicit URL through a
one-time conversion; data-20261007 and earlier remain available for
historical reproduction.

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

The terminal and MCP are validated outside the checkout. Linux/WSL is tested;
macOS uses the same POSIX implementation. Native Windows cancellation is not
validated. HTTP MCP and web modules are optional development prototypes.
The planned local browser workspace is not part of this release. Hosted
real sign-in, visitor OS-user/container isolation and public operation are
not completed. A figure the checker cannot connect to a tool value is marked
unverified; that is not a finding that it is wrong.

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
