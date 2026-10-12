# Connecting an AI account to Bench2Agent

Bench2Agent's own work is the data: the pinned revision, the six tools, the
counting rules and the verification of every quote and figure. The model
that reads the tools and writes the prose comes from an account the user
already has. This page records, per route, what the provider's own documents
permit, what Bench2Agent has actually executed, and what is known about
offering the route on a public web service. Nothing here is a legal opinion;
it quotes the documents and says what was run.

Checked on 2026-10-12 against the pages linked below. Re-check before a
public launch; both providers changed these terms during 2026.

| Route | Documented by the provider | Executed by Bench2Agent | Public hosting |
|---|---|---|---|
| **Claude Code, the user's own Claude login** (`bench2agent` connector `claude`) | Permitted: the unmodified binary, signed in through Anthropic's own flow. A platform may host it if each end user signs in with their own subscription or key and the platform does not pay for, resell or intermediate the usage, and agrees to the Commercial Terms. Collecting, storing or intermediating Claude.ai credentials or session tokens is forbidden. | **Yes, locally.** The web host on loopback and the terminal ran the robotics question through the installed `claude` with its own login, with Bench2Agent's pinned MCP server as the only tool; the server's call log was the trail; quotes and figures verified on the same revision. | **Possible under conditions, not built.** Needs one isolated Claude Code runtime per visitor (own `CLAUDE_CONFIG_DIR`) in which the visitor completes Anthropic's sign-in; Bench2Agent must never see the credential. Agreement to the Commercial Terms is required. |
| **Codex, the user's own ChatGPT login** (connector `codex`) | The Codex CLI's `codex login` is for the person running it. For other apps, OpenAI offers *Sign in with ChatGPT* with plan usage: "for open-source and locally hosted apps"; "a paid or remotely hosted app" must complete OpenAI's interest form. The token may drive the Codex app-server (`ACCESS_TOKEN`, provider `openai_chatgpt_plan`) or the public Responses API with `store: false, stream: true`. The self-hosted-VM page allows a remote VM only with OAuth completed on the user's machine and the credential transferred, and notes that per-host usage attribution and revocation "are not yet available". | **Yes, locally.** Same question, same MCP server, through the installed `codex exec --json` with its own ChatGPT login; trail from the server log; verified on the same revision. The *Sign in with ChatGPT* flow (dynamic registration, PKCE on a 127.0.0.1 callback, scopes `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`) is documented but **not implemented**. | **Not without approval.** A public Bench2Agent web is "a remotely hosted app": OpenAI's interest form first. |
| **Provider API key** (connector `api`) | The key owner's own agreement with the provider; no third-party login involved. | **Yes.** The terminal and the web host (service mode: the operator's key; user mode: a key sent per request, never stored) run Bench2Agent's own tool loop. | **Yes**, as a service-funded host with per-user limits, or with each user's own key. The only route available on a public host today. |

Sources: Anthropic, [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)
and [Authentication](https://code.claude.com/docs/en/authentication); OpenAI, [Sign in with ChatGPT token
sharing for open-source projects](https://developers.openai.com/siwc/token-sharing-open-source) and its
sub-pages (registration and sign-in, Codex app-server, self-hosted VMs, preview limitations).

## What every route shares

- **One revision.** The conversation's data revision is fixed when it is
  created. The CLI connectors receive Bench2Agent's stdio MCP server started
  with `--revision <id>`; the API connector calls the HTTP MCP endpoint of
  that revision. Verification runs on the same revision.
- **Bench2Agent records the loop it does not run.** When Claude Code or Codex
  owns the model/tool loop, the MCP server writes every call (name,
  arguments, result, revision) to a per-attempt log, and that log is the
  trail the saved turn carries. The final text alone is never trusted.
- **Credentials stay with the provider's tool.** The connectors read a
  login state (`claude auth status`, `codex login status`) and point the
  user at the provider's own sign-in command. They never read, copy or
  forward a credential. Per-account isolation uses the providers' own
  configuration-directory mechanisms (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`).
- **The same layer, two surfaces.** `bench2agent --connector claude` (or
  `codex`, or the default `api`) in the terminal and the connector choice in
  the web page call the same `bench2agent.runtime` connectors.

## What the local run showed (revision `0dd4b9bbee4b54d2`)

Question: *What benchmarks are researchers using in robotics lately?*

| Connector | Elapsed | Tool calls (from the MCP log) | Quotes verified | Figures recomputed |
|---|---|---|---|---|
| Claude Code (direct) | 63 s | `benchmark_scope`, `benchmark_usage` | 2 / 2 | 28 / 31 before the checker fix, 35 / 36 through the web afterwards |
| Codex | 61 s | `benchmark_scope`, `benchmark_usage`, 5 × `benchmark_evidence` | 3 / 3 | 7 / 8 |

Both answers named LIBERO 80, SIMPLER 41, Meta-World 29 on 509 parsed papers,
with repository links and introducing-paper links where the tools had them
and "none reviewed" / "no confirmed location" where they did not. The
figures that did not recompute were arXiv ids and edition years inside
anchored text, which the checker now treats as names; the remaining miss is a
genuine one to inspect in the live check. A green mark says a number exists
in some tool output; the binding check in `docs/web-live-check.md` is still
needed.

## The three representative questions through the web (2026-10-12)

Same host, same revision `0dd4b9bbee4b54d2`, a visitor session on loopback,
both CLI connectors with the machine's own logins, questions run from the
page's API. Elapsed time is wall clock from request to verified turn; the
tools themselves took under 0.2 s per call (the CLI runs them, so their time
is inside the model column).

| Connector | Question | Elapsed | MCP calls | Quotes verified | Figures recomputed |
|---|---|---|---|---|---|
| claude | field | 72 s (model 70, checks 0.1) | 2 | 5/5 | 31/32 |
| claude | trend | 59 s (model 58, checks 0.1) | 3 | 3/3 | 8/11 |
| claude | new | 86 s (model 84, checks 1.3) | 6 | 2/2 | 27/27 |
| codex | field | 64 s (model 63, checks 0.1) | 7 | 2/2 | 16/16 |
| codex | trend | 41 s (model 40, checks 0.1) | 5 | 1/1 | 10/10 |
| codex | new | 75 s (model 72, checks 1.4) | 13 | 2/2 | 22/23 |

Binding check against the MCP logs: both connectors gave LIBERO 80, SIMPLER
41, Meta-World 29 on 509 parsed papers for the field question, and
178/1,888, 197/3,038, 165/4,237 for CIFAR-10 at ICLR 2024/2025/2026, each
attached to the right benchmark and edition. Every answer showed the
repository and introducing-paper links the tools had as separate URLs and
wrote "no confirmed location" / "none reviewed" where they did not; no link
was invented. The figures that did not recompute were, in every case, not
tool numbers: a row count the model chose ("top 12"), edition years and the
z threshold quoted from the rule text (the checker now reads numbers inside
returned text), two fragments of a date the model split across an anchor,
and totals the model summed over the 28 editions (58,616 parsed of 77,775
listed), which the checker does not derive. Quotes: 15 of 15 verbatim.

The terminal, through the same layer (`bench2agent ask --connector claude`),
gave the CIFAR-10 series with the same figures.

## The two sides, as built now

**Local = the terminal.** `pip install bench2agent` (the current code is not
on PyPI yet; install from the repository or a built wheel until a release),
then `bench2agent`. With no `--connector`, an interactive run looks for the
Claude Code and Codex CLIs signed in on the machine and uses the one it finds
(asks when both are); scripts (`bench2agent ask`) take `--connector` or the
saved choice, else the API key. Bench2Agent's install is separate from the
official CLIs' installs and logins. `--connector api` is the explicit
alternative.

**Hosted = the web.** `bench2agent web serve` offers one way to answer: the
visitor's own Claude account in a runtime of their own on the host. A visitor
opens the address, gets a session cookie (no name, no code), presses
*Connect your Claude account*, and the host runs the official
`claude auth login` in that runtime under a pseudo-terminal. What the CLI
prints is relayed to that visitor's browser and what the visitor types is
relayed back; the host records only the shape of the exchange (started, link
shown, awaiting input, success or failure), never a link, code or token.
Connecting is the visitor's access: without it they can read, not ask.
Disconnect stops any running question, runs the official `claude auth logout`
and removes the runtime directory; conversations stay.

The headless sign-in, as the CLI runs it in an empty configuration directory
(observed 2026-10-12, values masked):

```
Opening browser to sign in…
If the browser didn't open, visit: <Anthropic sign-in URL>
Paste code here if prompted >
```

The visitor opens that link in their own browser, signs in to Anthropic
there, and the browser shows a code to paste back. Relaying that exchange is
the part whose acceptability Bench2Agent has **not** confirmed with
Anthropic; the inquiry draft below asks exactly that. The flow has run end to
end only against a stub CLI in tests; the real sign-in needs a person at a
browser, which the owner can perform from the page.

### What one visitor's runtime is, and is not

Each visitor gets `data/bench2agent/visitors/<id>/home/` (mode 0700) as HOME
and working directory, with `home/.claude/` as `CLAUDE_CONFIG_DIR`, the
CLI's own credential store. The process environment is an allowlist (PATH,
locale, TLS and proxy variables) plus that HOME; the operator's keys, login
and environment never enter it, which a test checks by injecting an operator
key and asserting the CLI never sees it. The CLI runs with every built-in
tool disabled (`--tools ""`), only Bench2Agent's pinned MCP server
(`--strict-mcp-config`, six read-only tools), and no other settings source;
a live check confirmed the shell is gone and the MCP tools remain.

This is directory and environment isolation. It separates logins, sessions
and settings between visitors; it is **not** an OS-user or container
boundary, because this host cannot create users without a password. The
stronger boundary is a deployment step. Limits: at most 50 runtimes and 4
concurrent CLI processes per host, idle runtimes removed after 30 days
(after the official logout); all configurable.

### Same-account tests, stated plainly

The automated tests use a stub CLI with two visitors and show that logins,
runtimes and conversations are kept apart and that a second visitor is not
connected when the first is. A real check with two different Anthropic
accounts has not been done: the owner has one account. Signing one account
into two runtimes would show directory and conversation separation, not
that two accounts' authentication and usage are separated. That remains a
beta item.

### Recovery rules

After a cancel, a failure, an unverifiable turn or a save conflict, the
CLI's own session is not resumed. The next question starts a fresh session
whose prompt carries only the turns Bench2Agent saved **and verified**: an
unverifiable answer is kept for the reader, marked as such, and never enters
any model context. Tested as a chain: unverifiable answer, next question,
cancel, resume, on the web and on a real pseudo-terminal (where Ctrl+C ends
the child CLI and the next question goes through). Conversation files are
saved conditionally under a lock, so a second terminal cannot overwrite a
newer turn, and a session's expected revision follows only its own
successful write. A turn is unverifiable when the MCP server left no log although the CLI
called tools, the log is damaged, it has fewer calls than the CLI showed, or
any call was answered from another revision; such a turn is saved as plain
text without marks and says so.

### Inquiry draft for Anthropic (no credentials, no account data)

> We run Bench2Agent, an open-source research tool. We would like to host
> the unmodified Claude Code binary so that each visitor of our web service
> answers questions with their own Claude subscription. Each visitor gets an
> isolated runtime (own HOME and CLAUDE_CONFIG_DIR, allowlisted environment,
> built-in tools disabled, only our read-only MCP server). To sign in, we run
> `claude auth login` in that runtime under a pseudo-terminal and relay its
> terminal output to the visitor's browser and the visitor's keystrokes
> back; the visitor completes sign-in on anthropic.com in their own browser
> and pastes the code the page shows. We do not read, copy, store or forward
> the resulting credential; the CLI keeps it in the visitor's runtime, and
> disconnect runs `claude auth logout` and deletes the runtime. We do not pay
> for or resell usage. Is this configuration consistent with the conditions
> under "Can customers offer Claude Code in their products?", and is relaying
> the sign-in exchange this way acceptable, or should the visitor interact
> with the CLI through another means?

## Local vs. public, stated plainly

**Local terminal: done.** `bench2agent` on a machine with Claude Code or
Codex signed in answers with that login, no Bench2Agent account or API key;
verified with real logins on the three representative questions, a resumed
follow-up and a cancel-then-follow-up on both CLIs.

**Hosted: a directory-isolation prototype, not yet exercised with a real
sign-in.** The visitor runtime, the relayed official sign-in, the restricted
CLI, the recorded tool log, verification on the conversation's revision,
disconnect (refuse new work, cancel, wait for jobs to end, official logout,
removal; pending cleanup when a job outlasts the wait) and per-job process
limits exist and are tested against a stub CLI. Isolation is by directory
and environment under one OS user, which keeps logins and conversations
apart but is not a boundary between processes; the agreed per-visitor OS
user or container is a deployment step on a host with the privilege. The
real sign-in from the page needs the owner at a browser, and the
acceptability of relaying the sign-in through a pseudo-terminal is not
settled by the documents alone.

**Public operation: not done.** Needs the owner's agreement to Anthropic's
Commercial Terms and an answer to the inquiry above, a stronger isolation
boundary (OS users or containers) on a host that permits it, HTTPS in front,
and a real two-account check. OpenAI's route is not offered on the web (a
remotely hosted app needs OpenAI's approval); Codex remains a terminal
connector.
