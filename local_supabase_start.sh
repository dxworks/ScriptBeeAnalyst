#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}=== Starting Local Supabase ===${NC}"

if [ ! -f "$SCRIPT_DIR/.env.supabase" ]; then
  echo -e "${RED}Error: .env.supabase not found. Run: cp .env.supabase.example .env.supabase${NC}"
  exit 1
fi

# Start containers
cd "$SCRIPT_DIR/local_supabase_deploy"
docker compose up -d

echo ""
echo -e "${BLUE}Waiting for services to become healthy...${NC}"

# Wait for the database to be ready (most critical service)
RETRIES=30
until docker exec supabase-db pg_isready -U postgres -h localhost > /dev/null 2>&1; do
  RETRIES=$((RETRIES - 1))
  if [ "$RETRIES" -le 0 ]; then
    echo -e "${RED}Timed out waiting for database.${NC}"
    echo -e "${YELLOW}Run 'docker compose ps' in local_supabase_deploy/ to check status.${NC}"
    exit 1
  fi
  sleep 2
done

echo -e "${GREEN}Database is ready.${NC}"

# Wait a bit more for Kong to be ready
RETRIES=15
until curl -sf http://localhost:8000 > /dev/null 2>&1; do
  RETRIES=$((RETRIES - 1))
  if [ "$RETRIES" -le 0 ]; then
    echo -e "${YELLOW}Kong not responding yet, but database is up. Studio may need a few more seconds.${NC}"
    break
  fi
  sleep 2
done

echo ""
echo -e "${GREEN}=== Local Supabase is running ===${NC}"
echo -e "${BLUE}Studio:    http://localhost:8000${NC}"
echo -e "${BLUE}API:       http://localhost:8000/rest/v1/${NC}"
echo -e "${BLUE}Auth:      http://localhost:8000/auth/v1/${NC}"
