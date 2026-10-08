# Bench2Agent releases

Bench2Agent distributes application code and research data separately. The
repository was renamed on 2026-10-08; the local checkout folder does not need
to change. The current source package is `bench2agent` version `0.3.0.dev0`.

## Current distribution

| Part | Status | Installation or contents |
|---|---|---|
| [GitHub repository](https://github.com/hyunyoungnam/Bench2Agent) | Available | Source, documentation and Apache-2.0 license |
| Current renamed application | Source installation | Follow the [terminal guide](terminal.md) |
| PyPI under the new package name | Pending | Use source installation until publication is announced |
| [Research data](https://github.com/hyunyoungnam/Bench2Agent/releases/tag/data-20261007) | Available | Benchmark snapshot, manifest and SHA-256 checksum |
| Earlier code releases | Preserved | Tagged artifacts retain their original names and package metadata |

GitHub repository redirects preserve the previous repository address. Earlier
tags and package-index uploads are immutable historical releases; renaming the
repository does not rename those packages or change their installed commands.
The renamed code is installed from the repository until a new release is made.

## Data releases

The `data-20261007` snapshot covers 28 editions and contains benchmark usage,
coverage and paper evidence. Its Bench2Agent download name is
`bench2agent-data.tar.gz`. The original asset remains available for existing
installations; the renamed asset has identical bytes and SHA-256. Renaming
does not change the snapshot identity or its schema.

`bench2agent data install` uses the release URL and checksum pinned in the
application. Code updates do not implicitly update installed data. Research
corpus files and generated bundles remain Git-ignored.

Future database releases will carry a database schema version, dataset revision,
manifest and checksum. They must reproduce the existing tool results before
becoming the default download. See [the database and hosting design](database-and-hosting.md).

## Code release checklist

1. Choose a release version and set it in `pyproject.toml` and
   `src/bench2agent/__init__.py`. A development version is not a public release.
2. Run Python and extension checks, inspect the renamed command and MCP entry,
   and verify representative queries on the released data.
3. Build wheel and source artifacts from the tested commit. Record checksums.
4. Install the wheel into a fresh environment and run `scripts/smoke_terminal.py`
   outside the checkout. Exercise the public data download and verify its hash.
5. Tag the tested commit and create a GitHub code release with artifacts,
   checksums and release notes, linking its tested data revision.
6. Publish the same artifacts to PyPI with a token authorized for the new
   project. A token scoped to another package does not authorize this project.
7. Verify package-index metadata and a fresh package-name installation before
   switching the README to the short PyPI installation command.

A new code release is separate from the name change. Historical code assets are
preserved. GitHub's [repository rename guide](https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository)
explains redirects; its [release guide](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
explains the relationship between tags and releases.
