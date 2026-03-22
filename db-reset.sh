#!/bin/bash
set -e

# ===========================================
# Supabase DB Reset
# ===========================================
# Drops all tables, clears storage, and reapplies all migrations.
# Target (local or remote) is read from .env.supabase (SUPABASE_TARGET).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MIGRATIONS_DIR="supabase/migrations"
DB_NAME="postgres"
STORAGE_BUCKET="serialized-files"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
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

echo -e "${RED}=== Supabase DB Reset (target: $SUPABASE_TARGET) ===${NC}"
echo -e "${YELLOW}WARNING: This will delete ALL data in the database and storage!${NC}"
read -p "Are you sure you want to continue? (yes/no): " -r
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
  echo "Aborted."
  exit 0
fi

# Check if migrations directory exists
if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo -e "${RED}Error: Migrations directory not found: $MIGRATIONS_DIR${NC}"
  exit 1
fi

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

  run_psql() {
    docker exec -i supabase-db psql -U postgres -d "$DB_NAME"
  }
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

  run_psql() {
    ssh_exec "docker exec -i supabase-db psql -U postgres -d $DB_NAME"
  }
  apply_migration() {
    local filename
    filename=$(basename "$1")
    ssh_exec "docker exec -i supabase-db psql -U postgres -d $DB_NAME < $REMOTE_TEMP/$filename"
  }
fi

# Step 1: Drop all tables in public schema
echo -e "${BLUE}Step 1/4: Dropping all tables in public schema...${NC}"
run_psql <<'EOF'
DO $$
DECLARE
  r RECORD;
BEGIN
  -- Drop all tables
  FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public')
  LOOP
    EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
  END LOOP;

  -- Drop all sequences
  FOR r IN (SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public')
  LOOP
    EXECUTE 'DROP SEQUENCE IF EXISTS public.' || quote_ident(r.sequence_name) || ' CASCADE';
  END LOOP;

  -- Drop all functions
  FOR r IN (SELECT routine_name FROM information_schema.routines WHERE routine_schema = 'public' AND routine_type = 'FUNCTION')
  LOOP
    EXECUTE 'DROP FUNCTION IF EXISTS public.' || quote_ident(r.routine_name) || ' CASCADE';
  END LOOP;

  -- Drop all types
  FOR r IN (SELECT typname FROM pg_type WHERE typnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public') AND typtype = 'c')
  LOOP
    EXECUTE 'DROP TYPE IF EXISTS public.' || quote_ident(r.typname) || ' CASCADE';
  END LOOP;
END $$;
EOF

echo -e "${GREEN}All tables dropped${NC}"

# Step 2: Clear storage bucket
echo -e "${BLUE}Step 2/4: Clearing storage bucket '$STORAGE_BUCKET'...${NC}"
run_psql <<EOF
DO \$\$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'storage' AND tablename = 'objects') THEN
    DELETE FROM storage.objects WHERE bucket_id = '$STORAGE_BUCKET';
    RAISE NOTICE 'Storage bucket cleared';
  ELSE
    RAISE NOTICE 'Storage schema not found, skipping';
  END IF;
END \$\$;
EOF

echo -e "${GREEN}Storage cleared${NC}"

# Step 3: Reset storage schema
echo -e "${BLUE}Step 3/4: Resetting storage schema...${NC}"
run_psql <<'EOF'
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'storage' AND tablename = 'buckets') THEN
    DELETE FROM storage.buckets;
    RAISE NOTICE 'Storage buckets cleared';
  END IF;
END $$;
EOF

echo -e "${GREEN}Storage schema reset${NC}"

# Step 4: Apply all migrations
echo -e "${BLUE}Step 4/4: Applying migrations...${NC}"

# For remote: copy migration files to remote temp dir
if [ "$SUPABASE_TARGET" != "local" ]; then
  REMOTE_TEMP=$(ssh_exec "mktemp -d")
  for migration in $(find "$MIGRATIONS_DIR" -name "*.sql" | sort); do
    scp_exec "$migration" "$REMOTE_HOST:$REMOTE_TEMP/$(basename "$migration")"
  done
fi

# Apply migrations in order
for migration in $(find "$MIGRATIONS_DIR" -name "*.sql" | sort); do
  echo -e "${GREEN}Applying: $(basename "$migration")${NC}"
  apply_migration "$migration" || {
    echo -e "${RED}Error applying migration: $(basename "$migration")${NC}"
    exit 1
  }
done

# Remote cleanup
if [ "$SUPABASE_TARGET" != "local" ]; then
  ssh_exec "rm -rf $REMOTE_TEMP"
fi

echo -e "${GREEN}All migrations applied${NC}"
echo -e "${GREEN}=== Database reset complete! ===${NC}"
