#!/bin/bash

# Test script for the new /projects/{id}/load endpoint

# ============================================
# CONFIGURATION - Update these IDs as needed
# ============================================
PROJECT_ID="16f4e41c-7dc5-403a-86a4-9ccb605c2b57"

echo "Testing Data Server Load Endpoint"
echo "=================================="
echo ""

if [ -z "$PROJECT_ID" ]; then
    echo "❌ PROJECT_ID not set in script. Please update the hardcoded value at the top of this file."
    exit 1
fi

echo "1. Health check..."
curl -s http://localhost:8001/health | jq '.'
echo ""

echo "2. Loading project: $PROJECT_ID..."
curl -s -X POST http://localhost:8001/projects/$PROJECT_ID/load | jq '.'
echo ""

echo "3. Check current project..."
curl -s http://localhost:8001/projects/current | jq '.'
echo ""

echo "4. Health check after load..."
curl -s http://localhost:8001/health | jq '.'
echo ""

echo "✅ Test complete!"
