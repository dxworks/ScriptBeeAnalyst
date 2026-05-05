#!/bin/bash
set -e

# ===========================================
# Supabase Environment Switcher
# ===========================================
# Reads .env.supabase and propagates values to:
#   1. Root .env (SUPABASE_URL, API_EXTERNAL_URL, SUPABASE_PUBLIC_URL)
#   2. web-ui/src/environments/environment.ts (url, anonKey)
#
# Usage:
#   ./env-switch.sh local    # Switch to local laptop
#   ./env-switch.sh remote   # Switch to home server via LAN

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_SUPABASE="$SCRIPT_DIR/.env.supabase"
ROOT_ENV="$SCRIPT_DIR/.env"
WEB_UI_ENV="$SCRIPT_DIR/web-ui/src/environments/environment.ts"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check .env.supabase exists
if [ ! -f "$ENV_SUPABASE" ]; then
  echo -e "${RED}Error: .env.supabase not found.${NC}"
  echo -e "${RED}Copy .env.supabase.example to .env.supabase and fill in the keys.${NC}"
  exit 1
fi

# Require a target argument
if [ -z "$1" ]; then
  echo -e "${RED}Usage: ./env-switch.sh <target>${NC}"
  echo -e "  ${GREEN}local${NC}    Laptop (localhost:8000)"
  echo -e "  ${GREEN}remote${NC}   Home server via LAN (192.168.0.100:8000)"
  exit 1
fi

TARGET="$1"
case "$TARGET" in
  local)
    URL="http://localhost:8000"
    URL_DOCKER="http://host.docker.internal:8000"
    ;;
  remote)
    URL="http://192.168.0.100:8000"
    URL_DOCKER="$URL"
    ;;
  *)
    echo -e "${RED}Error: Unknown target '$TARGET'. Use: local or remote${NC}"
    exit 1
    ;;
esac

# Update .env.supabase in-place
sed -i "s|^SUPABASE_TARGET=.*|SUPABASE_TARGET=$TARGET|" "$ENV_SUPABASE"
sed -i "s|^SUPABASE_URL=.*|SUPABASE_URL=$URL|" "$ENV_SUPABASE"
sed -i "s|^API_EXTERNAL_URL=.*|API_EXTERNAL_URL=$URL|" "$ENV_SUPABASE"
sed -i "s|^SUPABASE_PUBLIC_URL=.*|SUPABASE_PUBLIC_URL=$URL|" "$ENV_SUPABASE"

# Source variables from .env.supabase (handles values with special chars like = in JWT tokens)
SUPABASE_TARGET=""
SUPABASE_URL=""
API_EXTERNAL_URL=""
SUPABASE_PUBLIC_URL=""
SUPABASE_ANON_KEY=""
SUPABASE_SERVICE_KEY=""
JWT_SECRET=""

while IFS= read -r line; do
  # Skip comments and empty lines
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "${line// }" ]] && continue

  # Extract key and value (split on first =)
  key="${line%%=*}"
  value="${line#*=}"
  key="$(echo "$key" | xargs)"

  case "$key" in
    SUPABASE_TARGET)    SUPABASE_TARGET="$value" ;;
    SUPABASE_URL)       SUPABASE_URL="$value" ;;
    API_EXTERNAL_URL)   API_EXTERNAL_URL="$value" ;;
    SUPABASE_PUBLIC_URL) SUPABASE_PUBLIC_URL="$value" ;;
    SUPABASE_ANON_KEY)  SUPABASE_ANON_KEY="$value" ;;
    SUPABASE_SERVICE_KEY) SUPABASE_SERVICE_KEY="$value" ;;
    JWT_SECRET)         JWT_SECRET="$value" ;;
  esac
done < "$ENV_SUPABASE"

# Validate required variables
if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_ANON_KEY" ] || [ -z "$SUPABASE_TARGET" ]; then
  echo -e "${RED}Error: Missing required variables in .env.supabase${NC}"
  echo -e "${RED}Required: SUPABASE_TARGET, SUPABASE_URL, SUPABASE_ANON_KEY${NC}"
  exit 1
fi

echo -e "${BLUE}=== Supabase Environment Switch ===${NC}"
echo -e "  Target: ${GREEN}$SUPABASE_TARGET${NC}"
echo -e "  URL:    ${GREEN}$SUPABASE_URL${NC}"

# Step 1: Update root .env
if [ -f "$ROOT_ENV" ]; then
  sed -i "s|^SUPABASE_URL=.*|SUPABASE_URL=$SUPABASE_URL|" "$ROOT_ENV"
  sed -i "s|^SUPABASE_URL_DOCKER=.*|SUPABASE_URL_DOCKER=$URL_DOCKER|" "$ROOT_ENV"
  sed -i "s|^API_EXTERNAL_URL=.*|API_EXTERNAL_URL=$API_EXTERNAL_URL|" "$ROOT_ENV"
  sed -i "s|^SUPABASE_PUBLIC_URL=.*|SUPABASE_PUBLIC_URL=$SUPABASE_PUBLIC_URL|" "$ROOT_ENV"
  echo -e "  ${GREEN}.env updated${NC}"
else
  echo -e "  ${YELLOW}Warning: .env not found, skipping${NC}"
fi

# Step 2: Regenerate web-ui environment.ts
mkdir -p "$(dirname "$WEB_UI_ENV")"
cat > "$WEB_UI_ENV" << EOF
export const environment = {
  production: false,
  supabase: {
    url: '$SUPABASE_URL',
    anonKey: '$SUPABASE_ANON_KEY',
  },
  dataServerUrl: 'http://localhost:8001',
};
EOF
echo -e "  ${GREEN}environment.ts updated${NC}"

echo ""
echo -e "${GREEN}Switch complete!${NC}"
echo -e "${YELLOW}Remember to restart any running services (web-ui, chat-ui, data-server).${NC}"

if [ "$SUPABASE_TARGET" = "local" ]; then
  echo -e "${BLUE}Start local Supabase:  cd local_supabase_deploy && docker compose up -d${NC}"
else
  echo -e "${BLUE}Stop local Supabase:   cd local_supabase_deploy && docker compose down${NC}"
fi
