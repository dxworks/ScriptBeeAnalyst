#!/bin/bash

# Test script for the new /projects/{id}/load endpoint

echo "Testing Data Server Load Endpoint"
echo "=================================="
echo ""

# Get project ID from .env file
PROJECT_ID=$(grep GRAPH_PROJECT_ID ../.env | cut -d '=' -f2)

if [ -z "$PROJECT_ID" ]; then
    echo "❌ GRAPH_PROJECT_ID not found in .env file"
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
