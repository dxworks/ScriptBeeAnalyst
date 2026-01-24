#!/bin/bash

# Test script to unload the current project from memory

echo "Unloading Current Project from Memory"
echo "======================================"
echo ""

# Get project ID from .env file
PROJECT_ID=$(grep GRAPH_PROJECT_ID ../.env | cut -d '=' -f2)

if [ -z "$PROJECT_ID" ]; then
    echo "❌ GRAPH_PROJECT_ID not found in .env file"
    exit 1
fi

echo "1. Check current project before unload..."
curl -s http://localhost:8001/projects/current | jq '.'
echo ""

echo "2. Unloading project: $PROJECT_ID..."
curl -s -X DELETE http://localhost:8001/projects/$PROJECT_ID/unload | jq '.'
echo ""

echo "3. Check current project after unload..."
curl -s http://localhost:8001/projects/current | jq '.'
echo ""

echo "4. Health check after unload..."
curl -s http://localhost:8001/health | jq '.'
echo ""

echo "✅ Unload test complete!"
echo ""
echo "💡 Now run ./dev-test.sh to see if queries fail without a loaded project"
