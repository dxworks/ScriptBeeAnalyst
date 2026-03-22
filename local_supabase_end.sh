#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${RED}=== Stop Local Supabase ===${NC}"
echo -e "${YELLOW}This will stop and remove all Supabase containers, networks, and volumes.${NC}"
read -p "Are you sure? (yes/no): " -r
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
  echo "Aborted."
  exit 0
fi

cd "$SCRIPT_DIR/local_supabase_deploy"

echo -e "${BLUE}Stopping Supabase services...${NC}"
docker compose down -v

echo ""
echo -e "${GREEN}=== Local Supabase stopped ===${NC}"
