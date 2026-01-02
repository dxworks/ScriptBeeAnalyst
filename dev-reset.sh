#!/bin/bash
# dev-reset.sh - Clean restart of development services
# Usage: ./dev-reset.sh
# Note: Supabase runs on jarvis server (192.168.0.100:8000)

set -e

echo "🔍 Checking connection to Supabase on jarvis..."
if curl -s -o /dev/null -w "%{http_code}" http://192.168.0.100:8000/ | grep -q "200\|404"; then
  echo "✅ Supabase is reachable on jarvis"
else
  echo "❌ Cannot reach Supabase on jarvis (192.168.0.100:8000)"
  echo "   Make sure you're on the same network or VPN is connected"
  exit 1
fi

echo "🛑 Stopping data-server container..."
docker compose --env-file .env -f data-server/docker-compose.yml down 2>/dev/null || true

echo "🚀 Starting Data Server..."
docker compose --env-file .env -f data-server/docker-compose.yml up -d

echo ""
echo "✅ Development environment ready!"
echo ""
echo "Services:"
echo "  • Supabase (jarvis): http://192.168.0.100:8000"
echo "  • Data Server:       http://localhost:8001"
echo "  • Swagger UI:        http://localhost:8001/docs"
echo ""
echo "To start Angular: cd web-ui && npm start"
