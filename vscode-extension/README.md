# Bellwether for VS Code

This extension starts the existing Bellwether Python service on a free local
port and opens benchmark conversation and supporting paper evidence inside VS Code. The service and its
data run in the workspace extension host, so remote workspaces need their own
Bellwether install and data bundle. The extension contains no corpus data.

## Try it from this checkout

1. Open the Bellwether repository in VS Code. Ensure this checkout has
   `reports/index.html`, `reports/chat.html`, and the processed data bundle.
2. Run **Run Bellwether Extension** with F5. A second VS Code window opens.
3. In that window, run **Bellwether: Ask About Benchmarks** or **Bellwether: Explore Papers**
   from the Command Palette. The extension starts the local service for you.
4. Use **Bellwether: Open in Browser** if a download or outside link needs the
   system browser. **Bellwether: Stop Local Server** stops the process.

If the workspace is not a complete Bellwether checkout, the extension looks in
`~/.bellwether`. Set `bellwether.installPath` to an absolute checkout or install
path to select another copy. Set `bellwether.pythonPath` if Python is not on the
extension host's PATH. If chat cannot find the Codex or Claude CLI, set
`bellwether.extraPath` to the directory containing that executable.
`bellwether.useMeilisearch` can disable the optional
search engine for a lightweight run. The **Bellwether** Output channel shows
startup errors and server output.

Open chat settings to select Codex or Claude Code and check login status.
**Bellwether: Sign In with Codex** opens `codex login --device-auth` in an
integrated terminal; **Bellwether: Sign In with Claude Code** opens
`claude auth login`. Finish the official CLI flow and click **Refresh status**.
The CLIs must already be installed in the workspace extension host (including
SSH/WSL workspaces). Bellwether does not read credential files, collect tokens,
or modify global Codex MCP settings. Both agents receive the current Python
interpreter and local MCP configuration for that process.

This is a local interface to the user's installed coding agent, not a custom
Anthropic subscription OAuth service. A distributed commercial account flow
would need the provider's supported integration and authorization. See
[Codex authentication](https://learn.chatgpt.com/docs/auth) and the
[Claude Agent SDK authentication boundary](https://code.claude.com/docs/en/agent-sdk/overview).

Full-text usage needs `data/processed/benchmark_snapshot.json` in the data
bundle. Build it after extraction and identity review with
`PYTHONPATH=src python3 -m bellwether benchmarks` on the build machine.
Without it, introduction claims can still be listed, but usage and adoption
are unavailable. The chat never substitutes abstract mentions for test use.
The [benchmark conversation guide](../docs/benchmark-chat.md) describes the
tools, evidence protocol, scope limits and representative checks.

No npm packages are needed to run the extension in a development host. Build
a local VSIX with `python3 vscode-extension/build_vsix.py`, then install the
result with VS Code's **Extensions: Install from VSIX...** command. The VSIX
contains only the extension code, not the corpus or Python runtime. For a
Marketplace release, use the official `@vscode/vsce` tool and publishing flow.

The current `/datasets/*.csv` downloads contain paper/source inventories. The
per-paper download of actual evaluation records remains the separate work
described in the repository's `docs/evaluation-data-downloads.md`.

## Runtime check

From the repository root, run `node --test vscode-extension/test/runtime.test.js`.
This checks bundle selection and launches the real Python server without
Meilisearch. On macOS, if Node is not installed but VS Code is, its Electron
binary can run the same test with `ELECTRON_RUN_AS_NODE=1`.
