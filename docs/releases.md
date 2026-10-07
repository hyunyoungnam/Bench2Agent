# BenchTrend releases

A public repository, a code release, a data release, and a package-index
publication serve different purposes. Making the repository public lets
people read and install the source; it does not publish all the other parts.

## Current distribution

As of 2026-10-07:

| Part | Status | What a user gets |
|---|---|---|
| Public GitHub repository | Available | Source code, documentation, and Apache-2.0 license |
| Code release [`v0.2.0`](https://github.com/hyunyoungnam/BenchTrend/releases/tag/v0.2.0) | Available (2026-10-07) | A fixed code version, installation artifacts, and release notes |
| Data release `data-20261007` | Available | `benchtrend-data.tar.gz` and its `.sha256` checksum |
| [PyPI package](https://pypi.org/project/benchtrend/) | Available (0.2.0, 2026-10-07) | Package-name installation: `uv tool install benchtrend` |

The [README installation](../README.md#get-started) installs from PyPI and
the public data release.

## Code releases

A GitHub code release attaches a name such as `v0.2.0` to a Git tag pointing
to a particular commit. It gives users a version they can reinstall and
maintainers a version they can reproduce when investigating a problem.
It also creates a release page in GitHub's Releases section. Merely adding
a Releases link to the README does not create a release.

For the first code release, provide:

- `benchtrend-0.2.0-py3-none-any.whl`: the installable Python package.
- `benchtrend-0.2.0.tar.gz`: the Python source distribution.
- Checksums for both files.
- Release notes describing terminal conversations and MCP, supported Python
  versions, installation commands, and the tested data snapshot.

The wheel contains the code, not the corpus. Users can download it from the
release page and install it with `uv tool install ./benchtrend-0.2.0-py3-none-any.whl`.
The first release should describe the project's research-prototype status.

Before publishing, build and install the artifacts in a fresh environment,
exercise the published data download, and verify an MCP query outside the
source checkout. Tag the tested commit with the same version recorded in
`pyproject.toml` and `src/benchtrend/__init__.py`.

## Data releases

Data releases are dated independently from the code. The
[2026-10-07 release](https://github.com/hyunyoungnam/BenchTrend/releases/tag/data-20261007)
contains the benchmark snapshot and bundle manifest, plus a checksum file.
It includes counts, coverage, and paper evidence needed by both conversation
routes. It does not contain the external benchmarks' underlying test records.

Keep the date, snapshot identity, schema version, edition coverage, and
checksum in the release notes. An installation validates the bundle before
replacing its current data. A code fix need not require another data download;
a new corpus snapshot need not require a new code version when its schema is
compatible. Conversations retain their original snapshot identity.

## PyPI publication

Publishing the built Python distributions to PyPI makes the package available
by name; a GitHub release does not do this by itself. `0.2.0` was uploaded with
`uv publish dist/benchtrend-0.2.0*` — the same two files attached to the
GitHub release — using a PyPI API token held only in that command's
environment. The README installs with:

```bash
uv tool install benchtrend
```

The data installation remains a separate command. For the next version, use a
project-scoped token (the project now exists) and publish right after the
GitHub release, from the same `dist/` files.

## Cutting a code release

`v0.2.0` was cut this way, and the next one should be too:

1. Bump the version in `pyproject.toml` and `src/benchtrend/__init__.py`;
   run the tests.
2. Commit, then tag that commit: `git tag -a vX.Y.Z -m "..."` and push the
   tag.
3. Build from the tagged commit: `uv build` writes the wheel and the source
   distribution to `dist/`; write `SHA256SUMS` beside them.
4. Install the wheel into a fresh environment and run
   `scripts/smoke_terminal.py` with that environment's Python.
5. `gh release create vX.Y.Z dist/benchtrend-X.Y.Z* dist/SHA256SUMS --title ...
   --notes-file ...`, linking the data release the code was tested against.

6. `uv publish dist/benchtrend-X.Y.Z*` with a project-scoped PyPI token.

Keep code-version and data-date badges distinct so a data tag is not mistaken
for a software version.

See GitHub's official [release documentation](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
and [creating a release guide](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).
