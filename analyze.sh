#!/bin/bash
#
# Launch an OpenCode session for the currently loaded project.
#
# Usage:
#   ./analyze.sh
#
# Queries the data-server for the loaded project, creates a per-project
# workspace if needed, and launches OpenCode from it.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_SERVER_URL="${DATA_SERVER_URL:-http://localhost:8001}"
PROJECTS_DIR="$SCRIPT_DIR/analyzed_projects/projects"

# Query the data-server for the currently loaded project
RESPONSE=$(curl -s -w "\n%{http_code}" "$DATA_SERVER_URL/projects/current" 2>/dev/null || echo -e "\n000")
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "000" ]; then
    echo "Error: Could not reach data-server at $DATA_SERVER_URL"
    echo "Make sure the data-server is running (port 8001)."
    exit 1
fi

if [ "$HTTP_CODE" = "404" ]; then
    echo "No project is currently loaded in the data-server."
    echo "Load a project from the web UI first, then run this script again."
    exit 1
fi

if [ "$HTTP_CODE" != "200" ]; then
    echo "Error: Unexpected response from data-server (HTTP $HTTP_CODE)"
    echo "$BODY"
    exit 1
fi

# Extract project info
PROJECT_ID=$(echo "$BODY" | jq -r '.project_id')
PROJECT_NAME=$(echo "$BODY" | jq -r '.project_name')

if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" = "null" ]; then
    echo "Error: Could not extract project_id from response."
    exit 1
fi

if [ -z "$PROJECT_NAME" ] || [ "$PROJECT_NAME" = "null" ]; then
    PROJECT_NAME="project-${PROJECT_ID:0:8}"
fi

# Sanitize name for folder
FOLDER_NAME=$(echo "$PROJECT_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | sed 's/[^a-z0-9-]//g')
if [ -z "$FOLDER_NAME" ]; then
    FOLDER_NAME="project-${PROJECT_ID:0:8}"
fi

WORKSPACE="$PROJECTS_DIR/$FOLDER_NAME"

# Create workspace skeleton if it doesn't exist (one-time files: README, dirs)
if [ ! -d "$WORKSPACE" ]; then
    echo "Creating workspace for '$PROJECT_NAME'..."

    mkdir -p "$WORKSPACE/outputs" "$WORKSPACE/scripts"

    # Generate README
    cat > "$WORKSPACE/README.md" << EOF
# $PROJECT_NAME

- **Project UUID:** \`$PROJECT_ID\`
- **Workspace created:** $(date '+%Y-%m-%d %H:%M')

## Usage

Open your AI agent in this directory to analyze this project:

\`\`\`bash
./analyze.sh
\`\`\`

The agent has MCP tools to query the data-server.
See \`analyzed_projects/instructions/\` for data model documentation.
EOF

    echo "Workspace created: analyzed_projects/projects/$FOLDER_NAME"
fi

# Always (re)write the per-workspace opencode.json. If this file is missing
# OpenCode walks up to the parent analyzed_projects/opencode.json whose
# relative MCP command path won't resolve from the workspace cwd, and the
# scriptbee-data MCP server fails to start.
cat > "$WORKSPACE/opencode.json" << 'OJEOF'
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "scriptbee-data": {
      "type": "local",
      "command": ["../../mcp-server/.venv/bin/python", "../../mcp-server/server.py"],
      "environment": {
        "DATA_SERVER_URL": "http://localhost:8001"
      }
    }
  },
  "instructions": [
    "../../instructions/compass.md",
    "../../instructions/query-examples.txt",
    "../../instructions/plot-patterns.txt",
    "../../../data-server/src/common/kernel/entity.py",
    "../../../data-server/src/common/kernel/ref.py",
    "../../../data-server/src/common/kernel/kinds.py",
    "../../../data-server/src/common/kernel/graph.py",
    "../../../data-server/src/common/people/account.py",
    "../../../data-server/src/common/people/unified.py",
    "../../../data-server/src/common/projects/project.py",
    "../../../data-server/src/common/domains/git/models.py",
    "../../../data-server/src/common/domains/github/models.py",
    "../../../data-server/src/common/domains/jira/models.py",
    "../../../data-server/src/common/domains/code_structure/models.py",
    "../../../data-server/src/common/domains/duplication/models.py",
    "../../../data-server/src/common/domains/quality/models.py",
    "../../../data-server/src/common/domains/metrics_lizard/models.py",
    "../../../data-server/src/common/domains/components/models.py",
    "../../../data-server/src/common/domains/app_inspector/models.py",
    "../../../data-server/src/sandbox/inject.py",
    "../../../data-server/src/sandbox/helpers.py"
  ]
}
OJEOF

echo "Opening OpenCode for: $PROJECT_NAME ($PROJECT_ID)"
cd "$WORKSPACE" && exec opencode
