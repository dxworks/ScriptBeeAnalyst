#!/bin/bash
set -e

# ===========================================
# Supabase DB Push
# ===========================================
# Applies all migration files to the Supabase database.
# Target (local or remote) is read from .env.supabase (SUPABASE_TARGET).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MIGRATIONS_DIR="supabase/migrations"
DB_NAME="postgres"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

# Load target configuration
if [ ! -f "$SCRIPT_DIR/.env.supabase" ]; then
  echo -e "${RED}Error: .env.supabase not found. Run: cp .env.supabase.example .env.supabase${NC}"
  exit 1
fi

# Parse SUPABASE_TARGET from .env.supabase
SUPABASE_TARGET=""
while IFS= read -r line; do
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "${line// }" ]] && continue
  key="${line%%=*}"
  value="${line#*=}"
  key="$(echo "$key" | xargs)"
  if [ "$key" = "SUPABASE_TARGET" ]; then
    SUPABASE_TARGET="$value"
    break
  fi
done < "$SCRIPT_DIR/.env.supabase"

if [ -z "$SUPABASE_TARGET" ]; then
  echo -e "${RED}Error: SUPABASE_TARGET not set in .env.supabase${NC}"
  exit 1
fi

echo -e "${BLUE}=== Supabase DB Push (target: $SUPABASE_TARGET) ===${NC}"

# Check if migrations directory exists
if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo -e "${RED}Error: Migrations directory not found: $MIGRATIONS_DIR${NC}"
  exit 1
fi

# Count migration files
MIGRATION_COUNT=$(find "$MIGRATIONS_DIR" -name "*.sql" | wc -l)
if [ "$MIGRATION_COUNT" -eq 0 ]; then
  echo -e "${RED}Error: No migration files found in $MIGRATIONS_DIR${NC}"
  exit 1
fi

echo -e "${BLUE}Found $MIGRATION_COUNT migration file(s)${NC}"

# Define execution functions based on target
if [ "$SUPABASE_TARGET" = "local" ]; then
  # -------------------------------------------
  # LOCAL: Execute directly on local Docker
  # -------------------------------------------

  # Check local container is running
  if ! docker ps --format '{{.Names}}' | grep -q '^supabase-db$'; then
    echo -e "${RED}Error: supabase-db container is not running.${NC}"
    echo -e "${RED}Start it with: cd local_supabase_deploy && docker compose up -d${NC}"
    exit 1
  fi

  apply_migration() {
    docker exec -i supabase-db psql -U postgres -d "$DB_NAME" < "$1"
  }

else
  # -------------------------------------------
  # REMOTE: Execute via SSH to jarvis
  # -------------------------------------------
  REMOTE_HOST="jarvis"
  CONTROL_PATH="/tmp/ssh-control-$$"

  echo -e "${BLUE}Establishing SSH connection to $REMOTE_HOST...${NC}"
  ssh -o ControlMaster=yes -o ControlPath="$CONTROL_PATH" -o ControlPersist=10 -fN "$REMOTE_HOST"

  ssh_exec() {
    ssh -o ControlPath="$CONTROL_PATH" "$REMOTE_HOST" "$@"
  }
  scp_exec() {
    scp -o ControlPath="$CONTROL_PATH" "$@"
  }

  cleanup() {
    echo -e "${BLUE}Closing SSH connection...${NC}"
    ssh -O exit -o ControlPath="$CONTROL_PATH" "$REMOTE_HOST" 2>/dev/null || true
    rm -f "$CONTROL_PATH"
  }
  trap cleanup EXIT

  # Create temp dir and copy migration files to remote
  REMOTE_TEMP=$(ssh_exec "mktemp -d")
  echo -e "${BLUE}Copying migration files to $REMOTE_HOST...${NC}"
  for migration in $(find "$MIGRATIONS_DIR" -name "*.sql" | sort); do
    scp_exec "$migration" "$REMOTE_HOST:$REMOTE_TEMP/$(basename "$migration")"
  done

  apply_migration() {
    local filename
    filename=$(basename "$1")
    ssh_exec "docker exec -i supabase-db psql -U postgres -d $DB_NAME < $REMOTE_TEMP/$filename"
  }
fi

# Apply migrations in order
echo -e "${BLUE}Applying migrations to database '$DB_NAME'...${NC}"
for migration in $(find "$MIGRATIONS_DIR" -name "*.sql" | sort); do
  echo -e "${GREEN}Applying: $(basename "$migration")${NC}"
  apply_migration "$migration" || {
    echo -e "${RED}Error applying migration: $(basename "$migration")${NC}"
    echo -e "${RED}You may need to check the migration file or database state${NC}"
    exit 1
  }
done

# Remote cleanup
if [ "$SUPABASE_TARGET" != "local" ]; then
  echo -e "${BLUE}Cleaning up temporary files on remote server...${NC}"
  ssh_exec "rm -rf $REMOTE_TEMP"
fi

echo -e "${GREEN}All migrations applied successfully!${NC}"
