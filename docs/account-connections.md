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

## Local vs. public, stated plainly

Today Bench2Agent can be used **locally, without any Bench2Agent account or
API key**: the web host on this machine (or the terminal) uses the Claude
Code or Codex login already on the machine. A **public** web address can
today offer only the API-key route (service-funded or user-supplied). The
account-connected routes on a public host need, respectively, a per-visitor
Claude Code runtime under Anthropic's Commercial Terms, and OpenAI's approval
of a remotely hosted *Sign in with ChatGPT* app. Neither is built, and the
page shows those connectors as unavailable on a hosted deployment rather than
offering a button that is not backed.
