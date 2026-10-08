#!/usr/bin/env bash
# One-line install for Linux / macOS / Windows-via-WSL:
#
#   curl -fsSL https://raw.githubusercontent.com/hyunyoungnam/Bench2Agent/main/install.sh | bash
#
# While the repo is PRIVATE that URL is a 404 for everyone; a collaborator
# with the gh CLI signed in (gh auth login) runs instead:
#
#   gh api repos/hyunyoungnam/Bench2Agent/contents/install.sh --jq .content | base64 -d | bash
#
# and the clone below goes through gh's credentials. BENCH2AGENT_RELEASE=<tag> then
# fetches that release's bundle the same way (BENCH2AGENT_BUNDLE is for a public
# URL or a local file).
#
# What it does: clone the repo to ~/.bench2agent, create a private venv (PEP 668
# machines refuse bare pip), install the `bench2agent` command onto PATH, fetch the
# search-engine binary and keys. If BENCH2AGENT_BUNDLE (a file path or URL) is set,
# the data bundle is fetched too — otherwise `bench2agent fetch-data` is the one step
# left before `bench2agent serve`.
#
# Overrides, mainly for testing: BENCH2AGENT_HOME (install dir), BENCH2AGENT_REPO (clone
# source), BENCH2AGENT_BIN (where the bench2agent symlink goes).
set -euo pipefail

REPO="${BENCH2AGENT_REPO:-https://github.com/hyunyoungnam/Bench2Agent}"
# an app dir, not a workspace: hidden by default, like other installed tools.
# Everything inside stays inspectable — the data being auditable is a feature.
DIR="${BENCH2AGENT_HOME:-$HOME/.bench2agent}"
BIN="${BENCH2AGENT_BIN:-$HOME/.local/bin}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }

command -v git >/dev/null || { echo "git is required (apt/brew install git)"; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required (3.10+)"; exit 1; }
python3 - <<'EOF' || { echo "python 3.10+ is required"; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
EOF

# a private repo: git alone has no credentials, gh does. setup-git is
# idempotent and only registers gh as a credential helper for github.com.
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    gh auth setup-git >/dev/null 2>&1 || true
fi

if [ -d "$DIR/.git" ]; then
    say "updating existing install in $DIR"
    git -C "$DIR" pull --ff-only
else
    say "cloning into $DIR"
    git clone --depth 1 "$REPO" "$DIR"
fi

say "creating the serving venv"
if ! python3 -m venv "$DIR/.venv-serve" 2>/dev/null; then
    rm -rf "$DIR/.venv-serve"    # a half-made venv breaks the retry
    PYV=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
    echo "python3 -m venv failed — on Debian/Ubuntu/WSL run:"
    echo "    sudo apt install python${PYV}-venv"
    echo "then re-run this installer."
    exit 1
fi
"$DIR/.venv-serve/bin/pip" install -q -e "$DIR"

mkdir -p "$BIN"
ln -sf "$DIR/.venv-serve/bin/bench2agent" "$BIN/bench2agent"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "note: $BIN is not on PATH yet — open a new terminal (or:"
       echo "      source ~/.profile) — until then, use $BIN/bench2agent";;
esac

say "fetching the search engine + keys"
"$BIN/bench2agent" setup

if [ -n "${BENCH2AGENT_RELEASE:-}" ]; then
    say "fetching the data bundle (release $BENCH2AGENT_RELEASE, via gh)"
    "$BIN/bench2agent" fetch-data --release "$BENCH2AGENT_RELEASE"
    say "done — run: bench2agent serve"
elif [ -n "${BENCH2AGENT_BUNDLE:-}" ]; then
    say "fetching the data bundle"
    case "$BENCH2AGENT_BUNDLE" in
        http*) "$BIN/bench2agent" fetch-data --url "$BENCH2AGENT_BUNDLE";;
        *)     "$BIN/bench2agent" fetch-data --file "$BENCH2AGENT_BUNDLE";;
    esac
    say "done — run: bench2agent serve"
else
    say "done — next: bench2agent fetch-data --file <bundle.tar.gz>   then: bench2agent serve"
fi
