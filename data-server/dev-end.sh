#!/bin/bash

# Stop and remove data-server Docker containers
# Run from data-server directory: ./dev-end.sh

echo "Stopping and removing data-server containers..."
docker compose --env-file ../.env down

echo "Data-server containers stopped and removed."
