#!/bin/bash
set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${RED}=== Docker Full Cleanup ===${NC}"
echo -e "${YELLOW}This will stop and remove ALL Docker containers, volumes, and networks.${NC}"
read -p "Are you sure? (yes/no): " -r
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
  echo "Aborted."
  exit 0
fi

# Stop and remove all containers
CONTAINERS=$(docker ps -aq 2>/dev/null)
if [ -n "$CONTAINERS" ]; then
  echo -e "${BLUE}Stopping all containers...${NC}"
  docker stop $CONTAINERS
  echo -e "${BLUE}Removing all containers...${NC}"
  docker rm $CONTAINERS
  echo -e "${GREEN}All containers removed.${NC}"
else
  echo -e "${GREEN}No containers to remove.${NC}"
fi

# Remove all volumes
VOLUMES=$(docker volume ls -q 2>/dev/null)
if [ -n "$VOLUMES" ]; then
  echo -e "${BLUE}Removing all volumes...${NC}"
  docker volume rm $VOLUMES
  echo -e "${GREEN}All volumes removed.${NC}"
else
  echo -e "${GREEN}No volumes to remove.${NC}"
fi

# Remove all non-default networks
NETWORKS=$(docker network ls --filter type=custom -q 2>/dev/null)
if [ -n "$NETWORKS" ]; then
  echo -e "${BLUE}Removing all custom networks...${NC}"
  docker network rm $NETWORKS 2>/dev/null || true
  echo -e "${GREEN}All custom networks removed.${NC}"
else
  echo -e "${GREEN}No custom networks to remove.${NC}"
fi

echo ""
echo -e "${GREEN}=== Cleanup complete ===${NC}"
