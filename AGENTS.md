# Working in Bench2Agent

Updated 2026-10-22 after the 0.6.1 release. This is the working guide;
[CLAUDE.md](CLAUDE.md) records product direction, current state and follow-up
work. Public references: [browser workspace](docs/browser-interface.md),
[data design](docs/database-and-hosting.md), [counting rules](docs/benchmark-chat.md)
and [releases](docs/releases.md). Old instruction backups and dated task notes
are history, not current policy.

## Product direction

Terminal and browser are two interfaces to one local application. Terminal
implementation comes first; the browser is a designed research workspace with
structured benchmark cards, comparison tables and inspectable evidence. Both
use the same conversation service, machine owner's Claude Code or Codex CLI
login, SQLite data revisions, MCP tools and verifier. Do not duplicate the
model/counting loop or scrape terminal output for the browser.
Scope controls must submit explicit tool arguments; they must not silently
change an old answer's scope. Use American English for interface copy.

There is no Bench2Agent account or sign-up. Browser assets and the API run on
the user's machine; paid hosting, a Mac server and per-visitor provider login
are not prerequisites. AI usage follows the user's provider plan or explicit
API option. Public multi-user hosting is a deferred deployment, separate from
the released local browser. Benchmarks are the unit; papers are evidence;
frequency measures adoption, never quality. The model selects tools and
explains; code counts and verifies.

## Released state

- **Code:** 0.6.1, published on GitHub and PyPI on 2026-10-22 (tag `v0.6.1`
  at `de08604`). It is a code-only update to 0.6.0 (stricter number checks).
- **Data:** `data-20261021`, revision `1e1fa171722da693`, schema 2 and manifest
  format v2, unchanged since 0.6.0. The data tag and `v0.6.0` point to `60873ad`.
- **Coverage:** 61 editions of 11 conferences, 88,172 analyzed paper-edition
  observations and 9,334 catalog entries. Individually reviewed resource
  locations cover 315 benchmarks (336 entries); 70 unique paper-provided links
  are attached to 74 benchmarks. Reviews performed by Claude Code are AI
  reviews, not human reviews.
- **Interfaces:** `bench2agent` and `bench2agent --web` are released in the
  base, standard-library package. The browser needs no `server` extra.
  It shares saved conversations, follow-ups, cancellation and revision pinning
  with the terminal. Its evidence panel uses recorded tool results.
- **Security:** the browser binds to loopback, checks Host/Origin and requires
  a launch token on API requests. Keep the token out of logs and screenshots.
- **Optional prototypes:** HTTP MCP and `web serve` use the `server` extra.
  The earlier hosted visitor prototype is deferred; it is not the local
  browser and has no verified public operation or strong visitor isolation.
- **Platforms:** Linux/WSL is tested. macOS follows the implemented POSIX and
  default-browser paths but has not had a full device test; native Windows
  remains unvalidated.

The released tags remain on their release commit when later documentation
changes land on main. Do not move tags, rebuild published versions or change
the active data as part of an unrelated task.

## Rules for every change

- Preserve benchmark IDs, versions, aliases, homonyms and review history.
  Names are not keys; `HumanEval` and `HumanEval+` remain distinct.
- Paper identity uses official IDs or validated, track-scoped feed links.
  Both sourceid and uid must agree; inspect whole groups for author and
  official-ID conflicts. Titles propose candidates, never automatic merges.
- Benchmark identity across papers requires a reviewed `same_product`
  decision. Shared authors, citations or a repository only propose a relation.
  Name corrections require an explicit reviewed target and evidence.
- Introduction claims require reviewed source evidence. A title alone proves
  nothing; stale evidence requires re-review. First introduction in this
  corpus is not a claim of worldwide priority.
- Evaluation and training are separate. Adoption separates other authors,
  introducing authors involved and unknown relationships. Missing is not zero.
- Usage counts paper-edition observations; adoption counts unique paper IDs.
  Do not count joined evidence rows as papers. Use only explicitly stated
  roles from the dedicated arXiv HTML pass, not abstract mentions or citations.
- Show usage shares as **percentages** of the same query's analyzed-paper
  denominator, with one-decimal rounding and `<0.1%` for small positive shares.
  Missing/zero denominators mean unavailable. `per_1000` remains compatibility
  data for old clients and saved conversations, not the default presentation.
  Keep z statistics out of the default answer; explain trend states in words.
- `papers_with_unattributed_benchmark_mentions` (legacy alias
  `unattributed_papers`) counts observations with an unresolved mention. Those
  papers may also have confirmed benchmark use. Neither this value nor its
  complement counts papers with no stated benchmark; do not subtract it from
  the denominator to infer non-use.
- Preserve label origins. Proceedings volume IDs and unknown-origin labels
  are not research fields. Field-filtered answers disclose excluded coverage.
- Verbatim quotes keep their paper attribution; assembled table-cell evidence
  is not a verbatim sentence. Numeric checks use this turn's tool log and the
  same benchmark, edition, role, metric and scope. A passing check is not a
  guarantee of semantic correctness or benchmark quality.
- Verified resource locations and paper-provided links remain separate. Link
  existence or a name mention does not prove the benchmark relationship,
  downloadable data or record validity. Keep artifact type, verification,
  observed data status and check date separate. A 401/404 is an observation,
  not proof of privacy or deletion. Original and repaired addresses remain
  distinct and never borrow each other's observations.
- Published revisions are immutable; install and verify before activation.
  Saved conversations and their tools/checks stay pinned. Working processing
  state, retries, leases and conversations never enter the research database.
- On source re-check failure, preserve the previous good parse and archive.
  OAI datestamps trigger re-checks; HTML hashes and arXiv versions identify
  fetched sources. Never stamp old parses as newly fetched or erase claim
  history when text disappears.
- Preserve other contributors' changes. Keep storage changes and counting or
  extraction-rule changes in separate commits. Do not create a branch or
  worktree unless the owner requests one.
- Honor authorization already given for the task; do not repeatedly request
  approval for routine authorized work. Package/data publication, unattended
  scheduling and unrelated account/config changes need their own scope.
- Never inspect, copy, forward or log provider credentials or sign-in content.
  Never expose internal launch keys. Keep `.env`, chats, raw papers, DB exports,
  review caches and reports local.

## Follow-up work and boundaries

1. Verifier: 0.6.1 keeps confirmed, contradicted and unverified apart and binds
   a value to its lookup, scope, edition set and denominator field. The checker
   still reads a fixed list of cues, so some correct numbers stay unverified.
   Add each new case as a fixed-log regression test with its correct
   counterpart; never fix a false mark by loosening identity or scope checks.
2. Paper key sentences (planned for 0.7.0): for each analyzed paper, three of
   the authors' own sentences (why the work was needed, its key method, its
   result). They are selected by a model, verified verbatim against the
   source and labeled as AI selection. They come from a new extraction, not
   the August local-model files. They need a new data release and a tool
   called only when asked.
3. Validate macOS and native Windows on actual devices. Browser keyboard
   automation and the owner's physical-keyboard check are complete for the
   existing setup; they are not cross-platform validation.
4. The incremental pipeline is implemented, but the OS timer remains
   unregistered. Automatic pre-activation acceptance is still a follow-up;
   manual candidate comparisons used for releases are not that automatic gate.
5. Retention and leases remain planned: preserve recent N, active, referenced
   and in-use revisions, with leases outside immutable revision directories
   and one lock for reader registration and deletion decisions.
6. Exact task/config/split/revision metadata and file-verified benchmark
   record downloads remain separate future work (start with GSM8K/MATH-500).
   Existing resource links do not mean this work is complete.
7. Keep MCP registration scopes explicit: a project config may use the checkout
   while a user config uses an installed package. Do not remove either or
   switch the connector silently. Restart an MCP client after upgrading its
   installed server.

## Code map

- `src/bench2agent/cli.py`, `session.py`, `providers.py`, `runtime/`: launch,
  common conversation lifecycle and Claude Code/Codex/API connectors.
- `src/bench2agent/workspace/`: local browser API, assets and accessibility.
- `src/bench2agent/core/benchmarks.py`, `repository.py`, `revisions.py`: the
  six tools, SQLite schema/builder and immutable revision handling.
- `src/bench2agent/core/mcp.py`, `mcp_http.py`: stdio and optional HTTP MCP.
- `src/bench2agent/core/chat.py`, `figures.py`, `verify.py`, `policy.py`,
  `metrics.py`: answer checks, instructions and shared percentage calculation.
- `src/bench2agent/data.py`, `migration.py`: install, bundle, activate, migrate.
- `src/icml/`: collect, normalize, match, parse, extract, review and update.
- `config/`: benchmark registry, introduction reviews, reviewed match and
  location decisions, and paper links.
- `src/bench2agent/web/`: deferred hosted visitor prototype.

## Checks and repository hygiene

Run checks appropriate to the change; use the exit status, not the last line.
Documentation-only edits need factual/link checks, not a new full model run.

```bash
PYTHONPATH=src python3 -m bench2agent data status
PYTHONPATH=src python3 -m unittest discover -s tests
node --test vscode-extension/test/*.test.js tests/node/*.test.js
PYTHONPATH=src python3 scripts/regression_check.py --revision 1e1fa171722da693
```

Current revision pins: robotics/latest denominator 513; LIBERO/SIMPLER/
Meta-World 82/41/29; LIBERO share 16.0%; ICLR CIFAR-10 179/1,894, 197/3,060,
168/4,252 (2024-2026), shares 9.5%/6.4%/4.0%. Historical pins remain in
`scripts/regression_check.py`; never reuse them as future answers.
Packaging changes also need `scripts/smoke_terminal.py` in a fresh installed
wheel environment outside the checkout. Release checks are in
[docs/releases.md](docs/releases.md).

`CLAUDE.md` and `AGENTS.md` are shared, tracked instructions, by the owner's
2026-10-21 request. Do not put private notes or machine-specific records in
them. `.claude/`, `todo.md`, `.env`, generated data and institutional report
drafts remain ignored. Put reproducible work under an ignored repo-local
directory, not solely in a disposable session scratchpad. Never add those
ignored artifacts to a documentation commit.
