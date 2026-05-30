#!/bin/bash
set -e

# ===========================================
# Supabase DB Reset
# ===========================================
# Drops all tables, clears storage, and reapplies all migrations on the
# local Supabase Postgres container.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MIGRATIONS_DIR="supabase/migrations"
DB_NAME="postgres"
STORAGE_BUCKET="serialized-files"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${RED}=== Supabase DB Reset (local) ===${NC}"
echo -e "${YELLOW}WARNING: This will delete ALL data in the database and storage!${NC}"
read -p "Are you sure you want to continue? (yes/no): " -r
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
  echo "Aborted."
  exit 0
fi

if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo -e "${RED}Error: Migrations directory not found: $MIGRATIONS_DIR${NC}"
  exit 1
fi

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
for migration in $(find "$MIGRATIONS_DIR" -name "*.sql" | sort); do
  echo -e "${GREEN}Applying: $(basename "$migration")${NC}"
  apply_migration "$migration" || {
    echo -e "${RED}Error applying migration: $(basename "$migration")${NC}"
    exit 1
  }
done

echo -e "${GREEN}All migrations applied${NC}"
echo -e "${GREEN}=== Database reset complete! ===${NC}"
