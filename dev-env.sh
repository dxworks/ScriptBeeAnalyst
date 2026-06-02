#!/usr/bin/env bash
#
# dev-env.sh — tear down the ScriptBeeAssistant dev session.
#
# Stops and removes ALL containers in the stack, deletes the named volumes
# (Postgres data, serialized files, pickles) and any orphan containers.
#
# WARNING: this WIPES THE DATABASE. The next ./dev-start.sh boots from a
# completely clean slate (no projects, empty schema re-applied on startup).
#
set -euo pipefail

# Run from the repo root regardless of where the script is invoked from.
cd "$(dirname "$0")"

echo ">> Tearing down containers, volumes, and orphans (this wipes the DB)..."
docker compose down -v --remove-orphans

echo ">> Done. Run ./dev-start.sh for a fresh session."
