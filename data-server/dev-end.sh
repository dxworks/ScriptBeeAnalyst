#!/bin/bash

# Stop and remove data-server and processor Docker containers
# Run from data-server directory: ./dev-end.sh

echo "Stopping and removing data-server and processor containers..."
docker compose --env-file ../.env down

echo "Containers stopped and removed."
