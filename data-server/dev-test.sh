#!/bin/bash

# dev-test.sh - Test script for data-server running in Docker
# Sends various Python queries to the /execute endpoint

set -e

BASE_URL="http://localhost:8001"
BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}================================================${NC}"
echo -e "${BLUE}  Data Server Test Script${NC}"
echo -e "${BLUE}================================================${NC}"
echo ""

# Check if server is running
echo -e "${YELLOW}Checking server health...${NC}"
if ! curl -s "${BASE_URL}/health" > /dev/null; then
    echo -e "${RED}ERROR: Server not responding at ${BASE_URL}${NC}"
    echo -e "${RED}Make sure the data-server Docker container is running!${NC}"
    echo -e "${YELLOW}Run: cd data-server && docker compose up -d${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Server is running${NC}"
echo ""

# Function to execute Python code
execute_code() {
    local test_name="$1"
    local python_code="$2"

    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}Test: ${test_name}${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    # Create JSON payload
    local json_payload=$(jq -n --arg code "$python_code" '{code: $code}')

    # Send request
    local response=$(curl -s -X POST "${BASE_URL}/execute" \
        -H "Content-Type: application/json" \
        -d "$json_payload")

    # Check for error
    if echo "$response" | jq -e '.error' > /dev/null 2>&1; then
        echo -e "${RED}ERROR:${NC}"
        echo "$response" | jq -r '.error'
    else
        echo "$response" | jq -r '.output'
    fi

    echo ""
}

# Test 1: Basic statistics
execute_code "Basic Statistics" '
git_project = graph_data["git"]
jira_project = graph_data["jira"]
github_project = graph_data["github"]

print("=== Project Statistics ===")
print(f"Total Git commits: {len(git_project.git_commit_registry.all)}")
print(f"Total JIRA issues: {len(jira_project.issue_registry.all)}")
print(f"Total GitHub PRs: {len(github_project.pull_request_registry.all)}")
print(f"Total Git authors: {len(git_project.account_registry.all)}")
print(f"Total files tracked: {len(git_project.file_registry.all)}")
'

# Test 2: Top 5 most modified files
execute_code "Top 5 Most Modified Files" '
from collections import Counter
file_counter = Counter()
git_project = graph_data["git"]
for file in git_project.file_registry.all:
    fname = file.last_existing_name()
    file_counter[fname] = len(file.changes)
top_5_files = file_counter.most_common(5)
print("The 5 most modified files (with number of modifications):")
for fname, count in top_5_files:
    print(f"  {fname}: {count} modifications")
'

echo -e "${BLUE}================================================${NC}"
echo -e "${GREEN}✓ All tests completed!${NC}"
echo -e "${BLUE}================================================${NC}"
