#!/bin/bash
set -e

# ===========================================
# Supabase Environment Propagator
# ===========================================
# Reads .env.supabase and writes the local-Supabase URL + anon key into:
#   1. Root .env (SUPABASE_URL, SUPABASE_URL_DOCKER, API_EXTERNAL_URL,
#      SUPABASE_PUBLIC_URL)
#   2. web-ui/src/environments/environment.ts (url, anonKey)
#
# Only the local Docker stack under local_supabase_deploy/ is supported.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_SUPABASE="$SCRIPT_DIR/.env.supabase"
ROOT_ENV="$SCRIPT_DIR/.env"
WEB_UI_ENV="$SCRIPT_DIR/web-ui/src/environments/environment.ts"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [ ! -f "$ENV_SUPABASE" ]; then
  echo -e "${RED}Error: .env.supabase not found.${NC}"
  echo -e "${RED}Copy .env.supabase.example to .env.supabase and fill in the keys.${NC}"
  exit 1
fi

# Source variables from .env.supabase (handles values with = in JWT tokens)
SUPABASE_URL=""
API_EXTERNAL_URL=""
SUPABASE_PUBLIC_URL=""
SUPABASE_ANON_KEY=""
SUPABASE_SERVICE_KEY=""
JWT_SECRET=""

while IFS= read -r line; do
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "${line// }" ]] && continue

  key="${line%%=*}"
  value="${line#*=}"
  key="$(echo "$key" | xargs)"

  case "$key" in
    SUPABASE_URL)         SUPABASE_URL="$value" ;;
    API_EXTERNAL_URL)     API_EXTERNAL_URL="$value" ;;
    SUPABASE_PUBLIC_URL)  SUPABASE_PUBLIC_URL="$value" ;;
    SUPABASE_ANON_KEY)    SUPABASE_ANON_KEY="$value" ;;
    SUPABASE_SERVICE_KEY) SUPABASE_SERVICE_KEY="$value" ;;
    JWT_SECRET)           JWT_SECRET="$value" ;;
  esac
done < "$ENV_SUPABASE"

if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_ANON_KEY" ]; then
  echo -e "${RED}Error: Missing required variables in .env.supabase${NC}"
  echo -e "${RED}Required: SUPABASE_URL, SUPABASE_ANON_KEY${NC}"
  exit 1
fi

# The data-server container reaches the host's localhost via host.docker.internal.
URL_DOCKER="http://host.docker.internal:8000"

echo -e "${BLUE}=== Supabase Environment Propagate ===${NC}"
echo -e "  URL:    ${GREEN}$SUPABASE_URL${NC}"

# Step 1: Update root .env
if [ -f "$ROOT_ENV" ]; then
  # Portable in-place edit: GNU sed needs `-i`, BSD/macOS sed needs `-i ''`.
  # `-i.bak` + cleanup works on both.
  sed -i.bak "s|^SUPABASE_URL=.*|SUPABASE_URL=$SUPABASE_URL|" "$ROOT_ENV"
  sed -i.bak "s|^SUPABASE_URL_DOCKER=.*|SUPABASE_URL_DOCKER=$URL_DOCKER|" "$ROOT_ENV"
  sed -i.bak "s|^API_EXTERNAL_URL=.*|API_EXTERNAL_URL=$API_EXTERNAL_URL|" "$ROOT_ENV"
  sed -i.bak "s|^SUPABASE_PUBLIC_URL=.*|SUPABASE_PUBLIC_URL=$SUPABASE_PUBLIC_URL|" "$ROOT_ENV"
  rm -f "$ROOT_ENV.bak"
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
echo -e "${GREEN}Done.${NC}"
echo -e "${YELLOW}Remember to restart any running services (web-ui, data-server).${NC}"
echo -e "${BLUE}Start local Supabase: cd local_supabase_deploy && docker compose up -d${NC}"
