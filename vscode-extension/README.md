# Bellwether for VS Code (prototype)

This extension starts the existing Bellwether Python service on a free local
port and opens its chat and paper explorer inside VS Code. The service and its
data run in the workspace extension host, so remote workspaces need their own
Bellwether install and data bundle. The extension contains no corpus data.

## Try it from this checkout

1. Open the Bellwether repository in VS Code. Ensure this checkout has
   `reports/index.html`, `reports/chat.html`, and the processed data bundle.
2. Run **Run Bellwether Extension** with F5. A second VS Code window opens.
3. In that window, run **Bellwether: Open Chat** or **Bellwether: Explore Papers**
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
