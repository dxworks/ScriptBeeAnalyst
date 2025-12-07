#!/bin/bash
# dev-reset.sh - Clean restart of all Docker services
# Usage: ./dev-reset.sh

set -e

echo "🛑 Stopping all containers..."
docker stop $(docker ps -aq) 2>/dev/null || true

echo "🗑️  Removing all containers..."
docker rm $(docker ps -aq) 2>/dev/null || true

echo "🚀 Starting Supabase..."
docker compose --env-file .env -f supabase/docker-compose.yml up -d

echo "⏳ Waiting for Supabase to be ready..."
sleep 5

echo "🚀 Starting Data Server..."
docker compose --env-file .env -f data-server/docker-compose.yml up -d

echo ""
echo "✅ All services started!"
echo ""
echo "Services:"
echo "  • Supabase:    http://localhost:8000"
echo "  • Data Server: http://localhost:8001"
echo "  • Swagger UI:  http://localhost:8001/docs"
echo ""
echo "To start Angular: cd web-ui && npm start"
