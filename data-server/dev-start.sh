#!/bin/bash
set -euo pipefail

# Start the full ScriptBee backend for local development:
#   1. Colima            - container runtime (this Mac has no Docker Desktop)
#   2. Local Supabase    - Kong gateway :8000, Postgres :5432, Studio
#   3. data-server :8001 + processor
#
# Pair with `ng serve --port 4200` in ../web-ui to run the whole project.
# Run from the data-server directory: ./dev-start.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# 1. Ensure the container runtime (Colima) is up.
if ! docker info >/dev/null 2>&1; then
  if command -v colima >/dev/null 2>&1; then
    echo "Docker daemon not reachable - starting Colima..."
    colima start
  else
    echo "ERROR: Docker daemon not reachable and Colima is not installed." >&2
    echo "Install it with: brew install colima docker docker-compose" >&2
    exit 1
  fi
fi

# 2. Ensure the local Supabase stack is up (Kong gateway on :8000).
if curl -sf http://localhost:8000 >/dev/null 2>&1; then
  echo "Supabase already running on :8000."
else
  echo "Starting local Supabase stack..."
  ( cd "$ROOT_DIR/local_supabase_deploy" && docker compose up -d )
  echo "Waiting for Supabase Postgres to be ready..."
  until docker exec supabase-db pg_isready -U postgres -h localhost >/dev/null 2>&1; do
    sleep 2
  done
  echo "Supabase is ready (Studio/API: http://localhost:8000)."
fi

# 3. Start data-server (:8001) + processor, attached so logs are visible.
echo "Starting data-server and processor containers..."
cd "$SCRIPT_DIR"
docker compose --env-file "$ROOT_DIR/.env" up
