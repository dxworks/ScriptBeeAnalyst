#!/bin/bash

# Test script to unload the current project from memory

# ============================================
# CONFIGURATION - Update these IDs as needed
# ============================================
PROJECT_ID="16f4e41c-7dc5-403a-86a4-9ccb605c2b57"

echo "Unloading Current Project from Memory"
echo "======================================"
echo ""

if [ -z "$PROJECT_ID" ]; then
    echo "❌ PROJECT_ID not set in script. Please update the hardcoded value at the top of this file."
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
