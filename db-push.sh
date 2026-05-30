#!/bin/bash
set -e

# ===========================================
# Supabase DB Push
# ===========================================
# Applies all migration files to the local Supabase Postgres container.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MIGRATIONS_DIR="supabase/migrations"
DB_NAME="postgres"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}=== Supabase DB Push (local) ===${NC}"

if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo -e "${RED}Error: Migrations directory not found: $MIGRATIONS_DIR${NC}"
  exit 1
fi

MIGRATION_COUNT=$(find "$MIGRATIONS_DIR" -name "*.sql" | wc -l)
if [ "$MIGRATION_COUNT" -eq 0 ]; then
  echo -e "${RED}Error: No migration files found in $MIGRATIONS_DIR${NC}"
  exit 1
fi

echo -e "${BLUE}Found $MIGRATION_COUNT migration file(s)${NC}"

if ! docker ps --format '{{.Names}}' | grep -q '^supabase-db$'; then
  echo -e "${RED}Error: supabase-db container is not running.${NC}"
  echo -e "${RED}Start it with: cd local_supabase_deploy && docker compose up -d${NC}"
  exit 1
fi

apply_migration() {
  docker exec -i supabase-db psql -U postgres -d "$DB_NAME" < "$1"
}

echo -e "${BLUE}Applying migrations to database '$DB_NAME'...${NC}"
for migration in $(find "$MIGRATIONS_DIR" -name "*.sql" | sort); do
  echo -e "${GREEN}Applying: $(basename "$migration")${NC}"
  apply_migration "$migration" || {
    echo -e "${RED}Error applying migration: $(basename "$migration")${NC}"
    echo -e "${RED}You may need to check the migration file or database state${NC}"
    exit 1
  }
done

echo -e "${GREEN}All migrations applied successfully!${NC}"
