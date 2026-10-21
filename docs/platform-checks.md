# Platform checks

Linux and WSL are tested for 0.6.0. **macOS and native Windows have not been
checked on real devices.** A browser on Windows that opens a workspace running
inside WSL counts as a WSL check, not a native Windows one. Record each device
check separately, with the date, OS version, Python version, the Bench2Agent
version, the data revision and which official CLI (Claude Code or Codex) was
signed in.

| Check | macOS | Native Windows |
|---|---|---|
| Install from PyPI in a fresh environment; `bench2agent --version` | not checked | not checked |
| First launch downloads, verifies and activates the default data | not checked | not checked |
| A signed-in CLI is detected; a signed-out one is reported with its sign-in command | not checked | not checked |
| A question in the terminal saves with its tool log and checks | not checked | not checked |
| Ctrl+C cancels an unfinished answer, nothing is saved and the CLI child process ends | not checked | not checked |
| `/resume ID` continues a saved conversation on its pinned revision | not checked | not checked |
| `bench2agent --web` opens the default browser; `--no-open` prints the address | not checked | not checked |
| A browser question saves; Cancel question saves nothing and ends the CLI child process | not checked | not checked |
| A conversation continued from the terminal and the browser keeps one history | not checked | not checked |
| Two writers to one conversation: the stale one is refused (native Windows has no `fcntl` lock; only the conditional save protects) | not checked | not checked |
| Deleting a conversation moves it to `chats/.deleted/` and the terminal list drops it | not checked | not checked |
| `bench2agent mcp --connect claude` / `codex` registers a server the client can call | not checked | not checked |

Mark a row only after it was run on that device; leave anything not run as
not checked rather than inferring it from Linux results.
