#!/bin/bash

# Start data-server and processor Docker containers (attached mode - logs visible)
# Run from data-server directory: ./dev-start.sh

echo "Starting data-server and processor containers..."
docker compose --env-file ../.env up
