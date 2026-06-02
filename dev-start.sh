#!/usr/bin/env bash
#
# dev-start.sh — start a fresh ScriptBeeAssistant dev session.
#
# Rebuilds all images (so code changes are baked into app + processor) and
# brings the whole single-container stack up in the FOREGROUND, so logs from
# every service stream into this terminal. Stop with Ctrl-C.
#
# Pair with ./dev-env.sh to wipe everything (containers + DB) between sessions.
#
set -euo pipefail

# Run from the repo root regardless of where the script is invoked from.
cd "$(dirname "$0")"

# Docker on this Mac is provided by Colima — make sure the engine is reachable.
if ! docker info >/dev/null 2>&1; then
  echo ">> Docker engine not reachable — starting Colima..."
  colima start
fi

echo ">> Rebuilding images and starting the stack (Ctrl-C to stop)..."
exec docker compose up --build
