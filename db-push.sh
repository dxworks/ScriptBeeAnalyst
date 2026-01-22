#!/bin/bash
set -e

# Configuration
REMOTE_HOST="jarvis"
REMOTE_DB_NAME="postgres"  # Change this to your database name on jarvis
MIGRATIONS_DIR="supabase/migrations"
CONTROL_PATH="/tmp/ssh-control-$$"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Supabase DB Push ===${NC}"

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

# Create temporary directory on remote server
echo -e "${BLUE}Creating temporary directory on remote server...${NC}"
REMOTE_TEMP=$(ssh_exec "mktemp -d")

# Copy all migration files to remote server
echo -e "${BLUE}Copying migration files to $REMOTE_HOST...${NC}"
for migration in $(find "$MIGRATIONS_DIR" -name "*.sql" | sort); do
  filename=$(basename "$migration")
  echo -e "  - $filename"
  scp_exec "$migration" "$REMOTE_HOST:$REMOTE_TEMP/$filename"
done

# Apply migrations in order
echo -e "${BLUE}Applying migrations to database '$REMOTE_DB_NAME'...${NC}"
for migration in $(find "$MIGRATIONS_DIR" -name "*.sql" | sort); do
  filename=$(basename "$migration")
  echo -e "${GREEN}Applying: $filename${NC}"

  ssh_exec "docker exec -i supabase-db psql -U postgres -d $REMOTE_DB_NAME < $REMOTE_TEMP/$filename" || {
    echo -e "${RED}Error applying migration: $filename${NC}"
    echo -e "${RED}You may need to check the migration file or database state${NC}"
    exit 1
  }
done

# Cleanup remote temp directory
echo -e "${BLUE}Cleaning up temporary files on remote server...${NC}"
ssh_exec "rm -rf $REMOTE_TEMP"

echo -e "${GREEN}✓ All migrations applied successfully!${NC}"
