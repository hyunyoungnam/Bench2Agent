# Terminal and browser interfaces

Direction agreed with the owner on 2026-10-15. **This is a target design; a
first working slice exists in the development checkout (0.6.0.dev0) and is not
part of any release.** See *Current state* below for what is built and checked.
The terminal implementation comes first; both interfaces belong to one local
application. The browser can offer a more polished visual experience while
using the same data and answer rules. Public hosting is a separate decision.

## What the two tracks share

| Concern | Terminal | Local browser target |
|---|---|---|
| Interaction | Keyboard conversation and commands | Designed conversation with inspectable results |
| Execution | User's PC | User's PC; browser assets and API on one loopback origin |
| AI | Installed, signed-in Claude Code or Codex | The same connectors and login; no separate visitor runtime |
| Research data | Installed immutable SQLite revision | Same installation and revision policy |
| Answers | Common tools, counting and verification | Same service; richer presentation of its structured results |
| History and settings | Local installation | Shared persistence contract, with explicit migration where needed |

No Bench2Agent account, invite or mandatory API key is introduced. The
official AI CLI must still be installed separately; AI usage follows its
account's plan or the user's explicit API option. No dedicated paid web
backend is required. Users install the application and run a local process;
this is not an installation-free public website. A static project website
can show instructions or a labeled demo, but cannot by itself execute the
user's installed CLI.

The browser launcher should open the page automatically and handle startup,
an occupied port and shutdown clearly. `bench2agent --web` starts it in the
development checkout (not in 0.5.0). The existing `bench2agent web serve`
starts the earlier hosted visitor prototype; `bench2agent serve` starts the
paper-evidence browser. Neither command is already the target product merely
because it opens a browser. Existing `server` extras can be reused for an
implementation; keep web dependencies optional for terminal-only installs.

## Design and interaction

Conversation is the starting point. Do not replace it with a dashboard of
charts, a benchmark ranking, a paper catalog or a large configuration form.
Offer a clear question box and a few representative example questions. On a
wide screen, use a compact history sidebar, a readable conversation column
and an on-demand result/evidence panel. On narrower screens, collapse the
sidebar and show details in a drawer or stacked view; three simultaneous
columns are not required.

| User action | Browser response |
|---|---|
| Ask what benchmarks a field uses | A concise answer with compact benchmark cards: name, evaluation/training role, count and analyzed denominator, plus the relevant scope |
| Open a benchmark | A detail panel with available repository/dataset and introducing-paper links, then source sentences and paper attribution |
| Compare benchmarks or editions | A table using existing tool results, with each edition's denominator and explicit role; a chart is optional when it answers the question |
| Ask about new benchmarks | Reviewed introductions and external/self/unknown adoption, including zero adoption and the corpus-first-claim caveat |
| Inspect the answer's scope | Visible field/venue/year/role context and a data-details panel for coverage, label origins and the pinned revision |
| Continue or change context | Follow-up conversation; changing a scope control submits explicit arguments in a new turn rather than silently changing an old answer |

Use typography, restrained color, spacing, alignment and progressive
disclosure to make the evidence legible. Benchmark cards are structured data,
not images of model prose. Keep the central answer readable; long quotations,
tool trails and implementation identifiers are available on demand. Loading,
tool progress, cancellation, empty results, missing links, disconnected CLI,
unverifiable answers and recoverable errors need deliberate layouts, not
just a successful-answer mockup.

All controls must work by keyboard, have visible focus and readable contrast,
and keep verification understandable without color alone. Render content and
links safely. No frontend framework, animation system or chart library is
required by this design; reuse the useful existing page code before choosing
additional dependencies.

## Data and evidence in the interface

- Every card and comparison comes from typed tool results with benchmark
  IDs and scope. The browser never extracts numerical facts from free-form
  answer text to create authoritative data cards or calculates new counting
  rules. Request unsupported comparisons through the common query service.
- Scope controls use the actual labels and editions returned by the tools.
  Unknown metadata, unlabeled papers and missing links remain explicit; do
  not invent a location, treat unknown as zero, or rank on link coverage.
- Separate evaluation from training, external from self/unknown adoption,
  and data-location links from introducing-paper links. Explain absent links.
- Usage shares use percentages in both answers and panels, with the same
  analyzed-paper denominator and one-decimal rounding. A missing denominator
  stays unavailable; small positive shares appear as `<0.1%`, not zero use.
- A passed figure check is a mechanical comparison to the turn's evidence,
  not a benchmark-quality rating or a guarantee that every semantic binding
  is correct. Keep the source, edition, role and denominator inspectable.
- Show each answer's scope clearly. Keep the pinned revision and full trail
  in data details and exports. The sidebar shows the running application
  version; the dataset ID is explained in Scope, not used as the product version.
  Updates affect new conversations; an existing
  conversation never silently switches revision. Cross-revision differences
  must not be presented as year-to-year usage trends.
- Source sentences remain verbatim and tied to the paper/version; assembled
  table evidence is identified. An unverifiable turn stays visibly marked
  and does not re-enter model context, just as in the terminal.

## Implementation boundary

The common conversation service owns connector selection, session lifecycle,
tool execution and logging, verification, cancellation and conditional saves.
The CLI and a thin local web API adapt that service. Do not drive the terminal
via a PTY to scrape its display, fork the model loop in the browser, or expose
arbitrary SQL/shell execution. Normal use of the existing native CLI
connectors and their structured tool logs stays behind the service.

Reuse the installed research database, settings and conversation persistence.
The current terminal and earlier web prototype store histories differently;
cross-interface resume is a delivery target requiring an adapter or explicit
migration, not an existing guarantee. Retain conversation IDs, saved turns,
connector, pinned revision and verification trail, and reject stale concurrent
writes. Browser refresh/disconnection must not create a duplicate question or
silently cancel a job; explicit cancel follows the common lifecycle.

Serve assets and the API from the same loopback origin. Reject unintended
origins/hosts and protect mutating local requests without introducing a
sign-up screen. Do not expose the local user's CLI runtime on the LAN or
public internet as a side effect of enabling the browser. Provider
credentials stay with the official CLI; connection status is sufficient for
the settings screen. A local UI disconnection is not permission to log the
user out of their globally installed CLI or delete its configuration.

Keep ingestion and publication jobs apart from conversation requests. The UI
may install a verified published data bundle through the common data manager;
it does not scrape conferences or rebuild the corpus inside an HTTP request.

## Current state (development checkout, 2026-10-20)

Built (`src/bench2agent/workspace/`, standard library only): `bench2agent --web
[--port N] [--no-open]` starts a loopback server, prints the address with a
key minted for this launch and opens the browser (on WSL through the Windows
side; the address is printed if opening fails). Requests must carry the
expected Host, no foreign Origin, and the launch key on every API call; no
CORS. The page calls the same conversation service as the terminal
(`Session.ask`) and the same conversation files, so a conversation continues
in either interface; a copy read before another interface saved is refused.
Questions run as jobs apart from HTTP: a reload reattaches, a resend with the
same request id never runs twice, a closed tab does not cancel. Cancel and
save follow `session.TurnControl`, shared with the terminal: accepted before
the save, nothing is saved and the CLI's own session is not resumed; once the
save has started, the cancel reports that it is too late. The details panel
(Benchmarks, Compare, Evidence, Scope & data) is built from the answer's own
recorded tool results, never from its prose.

Design (2026-10-20, following Anthropic's frontend-design guidance): reading
serif Source Serif 4 for answers, titles and quoted sentences, Atkinson
Hyperlegible Next for interface, numbers and identifiers (both OFL, bundled
in `workspace/static/fonts` with their licences, about 100 KB; Hangul falls
back to the system face). One device carries the identity: verbatim quotes
and the benchmark names in evidence sentences are highlighted, figures the
checker could not match carry a dotted pencil underline. Evidence entries are
ledger rows with count, denominator and a share bar, not cards; no uppercase
labels. Bar widths are set through the CSSOM because the page's policy
refuses inline styles.

Checked: unit tests (guards, idempotent resend, dropped client, API cancel,
terminal/browser continuation and stale-copy refusal); a live Claude Code
question through the API (six tool calls, saved); a live cancel (nothing
saved, no CLI process left); the same conversation continued from the
terminal and read back by the page; the wheel installed outside the checkout
serves the page and API; screenshots at desktop width and 540 px reviewed
(headless Edge clamps below about 500 px).

Conversations can be deleted from the sidebar (the × beside each one, with a
confirmation). A deleted conversation leaves both the browser's list and
`bench2agent chats`; its file and tool logs move, under the same lock a save
takes, into `data/bench2agent/chats/.deleted/<id>-<UTC time>/`, from where it
can be restored by moving them back. Deleting is refused while a question is
running in that conversation, and a terminal that had it open can no longer
save to it.

Not yet: Windows-native locking (the conversation lock uses fcntl; on native
Windows only the conditional save protects), keyboard-only and contrast
review, an empty/disconnected-CLI review with a real signed-out CLI, the old
hosted store's conversations (kept, not migrated), and release documentation.

## Delivery order and acceptance

1. Stabilize the terminal and data/query/verification behavior. Browser layout
   exploration can run alongside this using clearly labeled fixtures or a
   pinned revision; it must not claim that a live connection already works.
2. Design and inspect the primary question, benchmark-detail and comparison
   screens, including loading, empty, missing-evidence and failure states.
3. Add the local launcher/API and wire the shared conversation service and
   native connectors. Adapt histories/settings without losing old data.
4. Run the same representative questions in terminal and browser on the same
   revision: robotics usage, the ICLR CIFAR-10 series, and reviewed new
   benchmarks adopted by other authors. Compare tool facts and attribution;
   model wording may differ. Bind expected numbers to that revision, not to
   an earlier screenshot. Test both supported local connectors.
5. Verify follow-up, reload, cancel/resume, conversation switching,
   cross-interface resume, concurrent-save rejection and data publication
   during a conversation. Inspect source links and quoted evidence end to
   end. Record actual timings rather than promising an unmeasured latency.
6. Inspect the rendered app at desktop and narrow widths with keyboard use;
   verify its static assets and launcher in an installed package outside the
   checkout before documenting the command as usable.

Public multi-user hosting, per-visitor runtimes, provider approval research,
HTTPS hosting and a Mac server are deferred deployment work, not acceptance
criteria for the local browser. Do not remove those prototypes or erase their
stored conversations as part of this documentation decision.
