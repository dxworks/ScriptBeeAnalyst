#!/bin/bash
set -e

# Configuration
REMOTE_HOST="jarvis"
REMOTE_DB_NAME="postgres"  # Change this to your database name on jarvis
MIGRATIONS_DIR="supabase/migrations"
CONTROL_PATH="/tmp/ssh-control-$$"
STORAGE_BUCKET="serialized-files"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${RED}=== Supabase DB Reset ===${NC}"
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

# Start SSH ControlMaster connection (ask for password once)
echo -e "${BLUE}Establishing SSH connection to $REMOTE_HOST...${NC}"
ssh -o ControlMaster=yes -o ControlPath="$CONTROL_PATH" -o ControlPersist=10 -fN "$REMOTE_HOST"

# Function to run SSH commands using the control connection
ssh_exec() {
  ssh -o ControlPath="$CONTROL_PATH" "$REMOTE_HOST" "$@"
}

# Function to copy files using the control connection
scp_exec() {
  scp -o ControlPath="$CONTROL_PATH" "$@"
}

# Cleanup function
cleanup() {
  echo -e "${BLUE}Closing SSH connection...${NC}"
  ssh -O exit -o ControlPath="$CONTROL_PATH" "$REMOTE_HOST" 2>/dev/null || true
  rm -f "$CONTROL_PATH"
}
trap cleanup EXIT

# Step 1: Drop all tables in public schema
echo -e "${BLUE}Step 1/4: Dropping all tables in public schema...${NC}"
ssh_exec "docker exec -i supabase-db psql -U postgres -d $REMOTE_DB_NAME" <<'EOF'
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

echo -e "${GREEN}✓ All tables dropped${NC}"

# Step 2: Clear storage bucket (if storage extension exists)
echo -e "${BLUE}Step 2/4: Clearing storage bucket '$STORAGE_BUCKET'...${NC}"
ssh_exec "docker exec -i supabase-db psql -U postgres -d $REMOTE_DB_NAME" <<EOF
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

echo -e "${GREEN}✓ Storage cleared${NC}"

# Step 3: Reset storage schema (recreate buckets table structure if needed)
echo -e "${BLUE}Step 3/4: Resetting storage schema...${NC}"
ssh_exec "docker exec -i supabase-db psql -U postgres -d $REMOTE_DB_NAME" <<'EOF'
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'storage' AND tablename = 'buckets') THEN
    DELETE FROM storage.buckets;
    RAISE NOTICE 'Storage buckets cleared';
  END IF;
END $$;
EOF

echo -e "${GREEN}✓ Storage schema reset${NC}"

# Step 4: Apply all migrations
echo -e "${BLUE}Step 4/4: Applying migrations...${NC}"

# Create temporary directory on remote server
REMOTE_TEMP=$(ssh_exec "mktemp -d")

# Copy all migration files to remote server
for migration in $(find "$MIGRATIONS_DIR" -name "*.sql" | sort); do
  filename=$(basename "$migration")
  scp_exec "$migration" "$REMOTE_HOST:$REMOTE_TEMP/$filename"
done

# Apply migrations in order
for migration in $(find "$MIGRATIONS_DIR" -name "*.sql" | sort); do
  filename=$(basename "$migration")
  echo -e "${GREEN}Applying: $filename${NC}"

  ssh_exec "docker exec -i supabase-db psql -U postgres -d $REMOTE_DB_NAME < $REMOTE_TEMP/$filename" || {
    echo -e "${RED}Error applying migration: $filename${NC}"
    exit 1
  }
done

# Cleanup remote temp directory
ssh_exec "rm -rf $REMOTE_TEMP"

echo -e "${GREEN}✓ All migrations applied${NC}"
echo -e "${GREEN}=== Database reset complete! ===${NC}"
