#!/bin/bash
set -euo pipefail

# Stop the ScriptBee backend started by dev-start.sh:
#   - Stops data-server + processor ONLY.
#   - Leaves the local Supabase stack running, untouched (stop it
#     separately with local_supabase_end.sh if you really need to).
#   - Leaves the Colima VM running (other tools may use it).
#       Halt it too with: STOP_COLIMA=1 ./dev-end.sh   (or: colima stop)
# Run from the data-server directory: ./dev-end.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon not reachable (Colima not running) - nothing to stop."
  exit 0
fi

echo "Stopping data-server and processor containers..."
cd "$SCRIPT_DIR"
docker compose --env-file "$ROOT_DIR/.env" down

# Supabase is intentionally left running — dev-end only stops the
# data-server. Stop Supabase separately with local_supabase_end.sh.

if [ "${STOP_COLIMA:-0}" = "1" ]; then
  echo "Stopping Colima VM..."
  colima stop
fi

echo "Backend stopped."
