# Live check of the web conversation

The automated tests drive the web host with a scripted model, so they prove
the plumbing (sign-in, ownership, revision pinning, verification, limits) but
not the quality of a real model's answers. This is the procedure for checking
three representative questions against a real model from the page, and what
to look at afterwards. It produces the measurements the beta criteria will be
set from; it does not assume them.

## Operator: prepare the private test address

```bash
pip install 'bench2agent[server]'                 # or build the Dockerfile
bench2agent data status                           # a verified active revision; note its id
export ANTHROPIC_API_KEY=...                      # service mode: the operator's key, never shown to users
bench2agent web invite add owner --uses 1         # prints an invite code once
bench2agent web serve                             # http://127.0.0.1:8780/  (put HTTPS in front for anyone remote)
```

With a provider key in the host's environment the credential mode is
`service`: every user's questions run on that key, under the per-user attempt
limits. Without one the mode is `user` and the page asks each user for their
own key, which travels from the browser through this backend to the model
provider for that request only and is neither stored nor logged.

## User: ask from the page

Open the address, enter the invite code and a name, **Sign in**. Choose the
provider, **New conversation**, then ask, one per conversation:

1. Field usage: *What benchmarks are researchers using in robotics lately?*
2. Change over editions: *How has CIFAR-10 use at ICLR changed across its last three editions?*
3. New benchmarks: *Which benchmarks introduced in this corpus are evaluated on by other authors, and where can I get them?*

An answer appears only after its quotes and figures were checked. The
**Sources and checks** line under it shows how many quotes were found
verbatim in the paper, how many figures recomputed from the tools, the data
revision, the elapsed time split into model, tools and checks, and the number
of tool calls. The tool trail (expand the line) lists every tool call with its
arguments.

## What to check, per answer

| Look at | Pass when |
|---|---|
| Figures (green/red) | every count or share in the prose is green: recomputed from a tool call. A red figure is a number no tool returned. |
| Figures (binding) | the green mark only says the number exists in some tool output. Open the trail, call the same tool with the same arguments (`bench2agent ask --json` on the same revision, or the MCP tool directly) and check that each number is attached to the right benchmark, edition, role and denominator. A swapped pair (LIBERO's count shown for SIMPLER) passes the mechanical check and fails here. |
| Quotes | every quoted sentence is green; a red quote is text the paper does not contain |
| Links | where the tool output has a location, the answer shows it as a clickable URL; where it has a reviewed introducing paper, the arXiv link, as a separate link. Where the output has neither, the answer says so ("no confirmed location", "no reviewed introducing paper in this corpus") rather than inventing or guessing a link. Not every benchmark has both; absence explained is a pass, absence filled in is a fail. |
| Denominators | the answer states the analysed editions and parsed-paper denominators and names venues outside a field filter. On revision `0dd4b9bbee4b54d2`, robotics on the latest editions is 509 parsed papers; LIBERO 80, SIMPLER 41, Meta-World 29; ICLR CIFAR-10 178/1,888, 197/3,038, 165/4,237. |
| Scope | no benchmark is called good, best or standard; frequency is described as adoption |
| Time | record the elapsed time and its split for each question. There is no pass threshold yet; the measured values set one. |

## Afterwards

```bash
bench2agent web attempts --user owner    # per question: outcome, total / model / tool / verification time, tool calls
```

Each attempt row carries the conversation id and attempt id, and the saved
conversation (in the page, or `GET /api/conversations/<cid>`) holds the
question, the segmented answer with every verification mark, the tool trail
and the same timing, so a reviewer can connect a slow or failed attempt to
what was asked and what the tools returned. Report, for each of the three
answers: the check line, every red mark with the text that failed, every link
shown or explained absent, the binding check for the headline numbers, and
the timing.

When done, `bench2agent web invite revoke owner` (or `web user revoke NAME`)
closes the door. In service mode no user ever saw the provider key; in user
mode the key existed only in the requests the user sent.
