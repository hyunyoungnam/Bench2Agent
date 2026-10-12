#!/bin/sh
# Install the pinned data release once, then serve the web host.
set -e
if ! bench2agent --home "$BENCH2AGENT_HOME" data status | grep -q '"available": true'; then
  echo "Installing the pinned data release into $BENCH2AGENT_HOME …" >&2
  bench2agent --home "$BENCH2AGENT_HOME" data install
fi
exec bench2agent --home "$BENCH2AGENT_HOME" web serve --host "${BENCH2AGENT_WEB_HOST:-0.0.0.0}" --port "${BENCH2AGENT_WEB_PORT:-8780}"
