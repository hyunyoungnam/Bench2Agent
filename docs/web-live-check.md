# Live check of the web conversation

The automated tests drive the web host with a scripted model, so they prove
the plumbing (ownership, revision pinning, verification, limits) but not the
quality of a real model's answers. This is the procedure for the owner to
run three representative questions against a real model from the page, and
what to look at afterwards. No credential is sent to anyone but the model
provider: the key is typed into the page and travels with each request only.

## Run

```bash
pip install 'bench2agent[server]'            # or: uv tool install 'bench2agent[server]'
bench2agent data status                      # a verified active revision; note its id
bench2agent web user add owner               # prints a bearer token once
bench2agent web serve                        # http://127.0.0.1:8780/
```

In the page: paste the bearer token and your model API key (Anthropic or
OpenAI), click **Connect**, choose the provider (and a model id if you want
one other than the default), **New conversation**, then ask, one per
conversation:

1. Field usage: *What benchmarks are researchers using in robotics lately?*
2. Change over editions: *How has CIFAR-10 use at ICLR changed across its last three editions?*
3. New benchmarks: *Which benchmarks introduced in this corpus are evaluated on by other authors, and where can I get them?*

Each answer appears only after its quotes and figures were checked. The
**Sources and checks** line under an answer shows how many quotes were found
verbatim in the paper and how many figures recomputed from the tools, the data
revision, the elapsed time and the number of tool calls.

## What to check, per answer

| Look at | Pass when |
|---|---|
| Figures | every count or share in the prose is green (recomputed from a tool call); a red figure means the model wrote a number no tool returned |
| Quotes | every quoted sentence is green; a red quote is text the paper does not contain |
| Links | benchmark rows carry a working repository / dataset / homepage link **and** an arXiv link to the introducing paper, as two separate links; no link invented for an entry whose tool output had none |
| Denominators | the answer states the analysed editions and parsed-paper denominators (robotics on revision `0dd4b9bbee4b54d2`: 509 parsed papers; LIBERO 80, SIMPLER 41, Meta-World 29) and says which venues are outside a field filter |
| Scope | the answer does not call any benchmark good, best or standard; frequency is described as adoption |
| Time | elapsed time per question (shown in the details line and in `bench2agent web attempts`); several tool rounds with a current model take tens of seconds, not minutes |

## Afterwards

```bash
bench2agent web attempts --user owner        # outcome, duration, tool calls per question; no credentials, no answer text
```

Each attempt row carries the conversation id; the saved conversation (in the
page, or `GET /api/conversations/<cid>` with the token) holds the question,
the segmented answer with every verification mark, the tool trail and the
timing, so a reviewer can connect a slow or failed attempt to what was asked
and what came back. Report the three answers' check lines, any red marks with
the text that failed, and the durations.

When done, `bench2agent web user revoke owner` disables the token. The
model key was never stored: it existed only in the requests you sent.
