#!/bin/bash

# Start data-server Docker containers (attached mode - logs visible)
# Run from data-server directory: ./dev-start.sh

echo "Starting data-server containers..."
docker compose --env-file ../.env up
