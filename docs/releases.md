# Bench2Agent releases

Code and research data are distributed separately. Version **0.4.0** adds the
shared Claude Code/Codex terminal runtime, JSON/SQLite repositories, immutable
installed revisions and answer checks based on each turn's actual tool log.

| Artifact | Contents |
|---|---|
| [PyPI 0.4.0](https://pypi.org/project/bench2agent/0.4.0/) | Standard-library terminal and stdio MCP; optional `server` dependencies |
| [GitHub v0.4.0](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/v0.4.0) | Identical wheel and source archive, checksums and release notes |
| [Data 2026-10-13](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/data-20261013) | JSON, SQLite, manifest, archive checksum and per-edition coverage CSV |

Install with `python -m pip install --upgrade bench2agent`, then `bench2agent`.
The official Claude Code or Codex CLI must be installed and signed in separately.
First interactive launch downloads data automatically. Both logins are supported;
API keys are an explicit alternative. See [the terminal guide](terminal.md).

## Research data

Data revision `e41da62f2fb3616f` has 61 editions of 11 conferences,
87,048 parsed paper-edition observations and 9,332 catalogue entries.
It expands usage coverage back to 2020 and adds NAACL. New introduction claims
in expanded editions await review, and field-label coverage is partial.
The release's `coverage.csv` reports listed, arXiv-matched, parsed and labelled
paper counts per edition. These are observations, not unique-paper counts.

The bundle (data format v1) carries its JSON reference and published SQLite
bytes. Its SHA-256 is
`9195fce6afd7e4ae7448be67bd9a6f17e5374bbbbe602f3900737088f69d3af2`.
In 0.4.0 the query backend is JSON by default and SQLite selectable; code
after 0.4.0 reads only the database and installs this bundle through a
one-time conversion (the next data release is a database-only bundle, data
format v2, which 0.4.0 cannot install).

Code upgrades preserve installed data and chats. `bench2agent data install`
installs the data URL/checksum pinned in that code version. Existing revisions
remain installed; saved conversations keep their revision. Restart a stdio MCP
server to pick up the active revision, or specify `--revision ID` explicitly.
The original `data-20261007` remains available for historical reproduction.

## Scope and limitations

The terminal and MCP are validated outside the checkout. Linux/WSL is tested;
macOS uses the same POSIX implementation. Native Windows cancellation is not
validated. HTTP MCP and web modules are optional development prototypes.
Hosted real sign-in, visitor OS-user/container isolation and public operation
are not completed. No public web deployment is included in this release.

## Release checklist

1. Set the version in `pyproject.toml` and `src/bench2agent/__init__.py`.
2. Freeze and verify one data revision; bundle its JSON, database and manifest.
3. Run Python, Node, backend parity and historical figure regression checks.
4. Build wheel/sdist, check metadata and install in a fresh environment outside
   the checkout. Exercise terminal connector selection and the published download.
5. Commit, wait for CI, tag and publish GitHub artifacts and checksums.
6. Publish the identical wheel/sdist to PyPI using a locally stored token.
7. Verify a fresh package-name installation, artifact hashes and data/tool queries.

Corpus exports, credentials, chats and raw paper archives remain Git-ignored.
Research bundles ship the snapshot's attributed evidence, not archived full texts.
