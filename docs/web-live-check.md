# Private hosted sign-in check

The packaged web code is a directory-isolation prototype under one OS user.
It has not completed a real hosted sign-in. Run this owner-only check on a
private machine; it does not establish public multi-user readiness or approval
of the PTY login relay. See [account-connections.md](account-connections.md).

## Prepare

Install the optional host and the official Claude Code CLI separately:

```bash
python -m pip install --upgrade 'bench2agent[server]'
bench2agent data install
bench2agent data status
bench2agent web serve
```

Open `http://127.0.0.1:8780/`. Windows-to-WSL loopback forwarding depends on
the machine's networking; if it fails, check the address rather than assume
login failed. No invite code, Bench2Agent signup or provider API key is needed.

## Connect, ask and disconnect

1. Press **Connect your Claude account**. Open the official login link and
   complete the prompt displayed by Claude Code through the page. The login
   code passes through the host's terminal relay. Authentication state remains
   in Claude Code's visitor runtime on the host until disconnected.
2. Create a conversation and ask the three questions below. Record the
   revision, tool trail, elapsed time and all verification results.
3. Ask a follow-up. Cancel a running answer, then ask another follow-up; the
   cancelled answer must not appear in the model's context.
4. Disconnect. Confirm running login/question processes ended, logout ran and
   the visitor runtime was removed. If cleanup is pending, wait for its actual
   completion; a status message alone is not success.
5. Reconnect and ask again. Check conversation ownership and revision pinning.

Representative questions:

- *What benchmarks are researchers using in robotics lately?*
- *How has CIFAR-10 use at ICLR changed from 2024 to 2026?*
- *Which benchmarks introduced in this corpus are evaluated on by other authors,
  and where can I get them?*

## Assess answers

Quotes must match source evidence. Figure marks mean the number occurred in
this turn's tool output, not that it is semantically attached to the correct
benchmark/year/role. Compare headline figures and denominators to the trail.
Do not require every incidental date or derived sum to be green.

Show each confirmed location and reviewed introducing paper as separate links
when available. Explain missing mappings; never invent them. Record timings
rather than impose an unmeasured target. State editions and missing coverage;
frequency does not establish benchmark quality.

Historical real-model reference figures belong only to revision
`0dd4b9bbee4b54d2`: robotics latest 509 papers, LIBERO/SIMPLER/Meta-World
80/41/29; ICLR CIFAR-10 178/1,888, 197/3,038, 165/4,237 for 2024–2026.
Use the actual installed revision for current checks. Expanded coverage and
incomplete new-introduction review/field labels must be reported separately.

Attempts and conversations in `data/bench2agent/web.sqlite` provide the
per-turn outcome, revision and timing. The page's tool trail and saved turn
provide the checks. A missing, damaged or mismatched MCP log means an
unverifiable answer: visible but excluded from follow-up context.
