#!/bin/bash
#
# Setup a per-project AI agent workspace.
#
# Usage:
#   ./setup-project.sh <project-uuid> [project-name]
#
# Creates a workspace folder under analyzed_projects/projects/ with a README
# containing project info. If no project-name is given, queries the data-server.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECTS_DIR="$SCRIPT_DIR/projects"
DATA_SERVER_URL="${DATA_SERVER_URL:-http://localhost:8001}"

PROJECT_ID="${1:?Usage: $0 <project-uuid> [project-name]}"
PROJECT_NAME="${2:-}"

# If no name provided, try to get it from data-server
if [ -z "$PROJECT_NAME" ]; then
    echo "Querying data-server for project info..."
    RESPONSE=$(curl -s "$DATA_SERVER_URL/projects/current" 2>/dev/null || echo "")

    if [ -z "$RESPONSE" ]; then
        echo "Warning: Could not reach data-server at $DATA_SERVER_URL"
        PROJECT_NAME="project-${PROJECT_ID:0:8}"
    else
        # Simple extraction - the project name isn't in /current, use project_id prefix
        PROJECT_NAME="project-${PROJECT_ID:0:8}"
    fi
fi

# Sanitize name for folder
FOLDER_NAME=$(echo "$PROJECT_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | sed 's/[^a-z0-9-]//g')
if [ -z "$FOLDER_NAME" ]; then
    FOLDER_NAME="project-${PROJECT_ID:0:8}"
fi

WORKSPACE="$PROJECTS_DIR/$FOLDER_NAME"

# Create directory structure
mkdir -p "$WORKSPACE/outputs" "$WORKSPACE/scripts"

# Generate README
cat > "$WORKSPACE/README.md" << EOF
# $PROJECT_NAME

- **Project UUID:** \`$PROJECT_ID\`
- **Workspace created:** $(date '+%Y-%m-%d %H:%M')

## Usage

Open your AI agent in this directory to analyze this project:

\`\`\`bash
cd analyzed_projects/projects/$FOLDER_NAME
opencode   # or: claude
\`\`\`

The agent has MCP tools to query the data-server.
See \`analyzed_projects/instructions/\` for data model documentation.
EOF

echo "Workspace created at: analyzed_projects/projects/$FOLDER_NAME"
echo ""
echo "Next steps:"
echo "  1. Make sure the data-server is running (port 8001)"
echo "  2. Load the project: curl -X POST $DATA_SERVER_URL/projects/$PROJECT_ID/load"
echo "  3. Open your AI agent:"
echo "     cd analyzed_projects/projects/$FOLDER_NAME && opencode"
echo "     # or: cd analyzed_projects && opencode"
