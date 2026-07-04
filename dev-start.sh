#!/usr/bin/env bash
#
# dev-start.sh — start a fresh ScriptBeeAssistant dev session with HOT RELOAD.
#
# Brings the whole stack up in the FOREGROUND (logs from every service stream
# into this terminal; Ctrl-C to stop), layering docker-compose.dev.yml on top
# of the base compose so code changes reload live without restarting:
#
#   * app       — Python edits reload the API (uvicorn --reload).
#   * processor — the worker auto-restarts on .py changes (watchfiles).
#   * web       — Angular UI hot-reloads in the browser (ng serve on :4200).
#
# In dev open  http://localhost:4200  for the hot-reloading UI. The API is on
# :8001 (the baked SPA at :8001/ is the static prod build — ignore it in dev).
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

echo ">> Starting the hot-reload stack (Ctrl-C to stop)..."
echo ">> UI (hot reload): http://localhost:4200   API: http://localhost:8001"
exec docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
