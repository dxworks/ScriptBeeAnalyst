#!/bin/bash
#
# Launch an AI coding session for the currently loaded project.
#
# Usage:
#   ./analyze.sh            # OpenCode web UI (default)
#   ./analyze.sh web        # OpenCode web UI (explicit)
#   ./analyze.sh tui        # OpenCode TUI
#   ./analyze.sh opencode   # OpenCode TUI (alias of tui)
#   ./analyze.sh claude     # Claude Code, same workspace + MCP + instructions
#
# Env:
#   CLAUDE_MODEL=opus       # model for the `claude` engine (default: opus)
#
# Queries the data-server for the loaded project, creates a per-project
# workspace if needed, and launches the chosen engine from it. Both engines
# get the same cwd, the same scriptbee-data MCP server, and the same
# data-model instruction files.

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

# Always (re)write the per-workspace opencode.json. Paths are absolute (built
# from $SCRIPT_DIR) so the scriptbee-data MCP server + instruction files resolve
# no matter where OpenCode anchors its working directory — `opencode web` in
# particular anchors at the nearest VCS root, not the launch cwd. The unquoted
# heredoc expands $SCRIPT_DIR / $DATA_SERVER_URL; "\$schema" is escaped so the
# shell leaves the JSON key intact.
cat > "$WORKSPACE/opencode.json" << OJEOF
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "openai/gpt-5.5",
  "mcp": {
    "scriptbee-data": {
      "type": "local",
      "command": ["$SCRIPT_DIR/analyzed_projects/mcp-server/.venv/bin/python", "$SCRIPT_DIR/analyzed_projects/mcp-server/server.py"],
      "environment": {
        "DATA_SERVER_URL": "$DATA_SERVER_URL"
      }
    }
  },
  "instructions": [
    "$SCRIPT_DIR/analyzed_projects/instructions/compass.md",
    "$SCRIPT_DIR/analyzed_projects/instructions/query-examples.txt",
    "$SCRIPT_DIR/analyzed_projects/instructions/plot-patterns.txt",
    "$SCRIPT_DIR/data-server/src/common/kernel/entity.py",
    "$SCRIPT_DIR/data-server/src/common/kernel/ref.py",
    "$SCRIPT_DIR/data-server/src/common/kernel/kinds.py",
    "$SCRIPT_DIR/data-server/src/common/kernel/graph.py",
    "$SCRIPT_DIR/data-server/src/common/people/account.py",
    "$SCRIPT_DIR/data-server/src/common/people/unified.py",
    "$SCRIPT_DIR/data-server/src/common/projects/project.py",
    "$SCRIPT_DIR/data-server/src/common/domains/git/models.py",
    "$SCRIPT_DIR/data-server/src/common/domains/github/models.py",
    "$SCRIPT_DIR/data-server/src/common/domains/jira/models.py",
    "$SCRIPT_DIR/data-server/src/common/domains/code_structure/models.py",
    "$SCRIPT_DIR/data-server/src/common/domains/duplication/models.py",
    "$SCRIPT_DIR/data-server/src/common/domains/quality/models.py",
    "$SCRIPT_DIR/data-server/src/common/domains/metrics_lizard/models.py",
    "$SCRIPT_DIR/data-server/src/common/domains/components/models.py",
    "$SCRIPT_DIR/data-server/src/common/domains/app_inspector/models.py",
    "$SCRIPT_DIR/data-server/src/sandbox/inject.py",
    "$SCRIPT_DIR/data-server/src/sandbox/helpers.py"
  ]
}
OJEOF

# OpenCode anchors its working directory at the nearest VCS root (walking up
# from the launch dir). `opencode web` has no path argument, so without a .git
# here it would anchor at the parent ScriptBee repo and never load this
# workspace's opencode.json. Give the workspace its own repo so IT is the VCS
# root. projects/* is gitignored by the parent repo, so this is inert there.
if [ ! -e "$WORKSPACE/.git" ]; then
    git -C "$WORKSPACE" init -q
fi

# ── Launch ───────────────────────────────────────────────────────────────────
# First arg selects engine / mode:
#   (none) | web   → OpenCode web UI   (default)
#   tui | opencode → OpenCode TUI
#   claude         → Claude Code, same workspace + MCP + instructions
ARG="${1:-web}"
case "$ARG" in
    claude)        ENGINE="claude" ;;
    web)           ENGINE="opencode"; OC_MODE="web" ;;
    opencode|tui)  ENGINE="opencode"; OC_MODE="tui" ;;
    *)             ENGINE="opencode"; OC_MODE="$ARG" ;;
esac

cd "$WORKSPACE"

if [ "$ENGINE" = "claude" ]; then
    MCP_DIR="$SCRIPT_DIR/analyzed_projects/mcp-server"
    CLAUDE_MODEL="${CLAUDE_MODEL:-opus}"

    # MCP config — absolute paths so the server starts regardless of cwd
    # (this is the relative-path fragility the opencode.json comment warns about).
    cat > "$WORKSPACE/.mcp.json" << CMEOF
{
  "mcpServers": {
    "scriptbee-data": {
      "command": "$MCP_DIR/.venv/bin/python",
      "args": ["$MCP_DIR/server.py"],
      "env": {
        "DATA_SERVER_URL": "$DATA_SERVER_URL"
      }
    }
  }
}
CMEOF

    # Workspace CLAUDE.md mirrors OpenCode's `instructions` array: each @-import
    # pulls a file's contents into the session context. Paths are relative to
    # this file (the workspace) — identical to the opencode.json entries above.
    cat > "$WORKSPACE/CLAUDE.md" << 'CDEOF'
# ScriptBee analysis session

You are analyzing the loaded ScriptBee project through the `scriptbee-data`
MCP tools. The files below are the data model plus query/plot references —
treat them as ground truth for entity kinds, refs, traits, relations, and the
`execute_code` sandbox helpers.

@../../instructions/compass.md
@../../instructions/query-examples.txt
@../../instructions/plot-patterns.txt
@../../../data-server/src/common/kernel/entity.py
@../../../data-server/src/common/kernel/ref.py
@../../../data-server/src/common/kernel/kinds.py
@../../../data-server/src/common/kernel/graph.py
@../../../data-server/src/common/people/account.py
@../../../data-server/src/common/people/unified.py
@../../../data-server/src/common/projects/project.py
@../../../data-server/src/common/domains/git/models.py
@../../../data-server/src/common/domains/github/models.py
@../../../data-server/src/common/domains/jira/models.py
@../../../data-server/src/common/domains/code_structure/models.py
@../../../data-server/src/common/domains/duplication/models.py
@../../../data-server/src/common/domains/quality/models.py
@../../../data-server/src/common/domains/metrics_lizard/models.py
@../../../data-server/src/common/domains/components/models.py
@../../../data-server/src/common/domains/app_inspector/models.py
@../../../data-server/src/sandbox/inject.py
@../../../data-server/src/sandbox/helpers.py
CDEOF

    echo "Opening Claude Code for: $PROJECT_NAME ($PROJECT_ID)"
    echo "  model: $CLAUDE_MODEL   mcp: scriptbee-data   cwd: $WORKSPACE"
    exec claude --model "$CLAUDE_MODEL" \
                --mcp-config "$WORKSPACE/.mcp.json" \
                --strict-mcp-config
fi

# Default engine: OpenCode (behavior unchanged)
if [ "$OC_MODE" = "web" ]; then
    echo "Opening OpenCode web UI for: $PROJECT_NAME ($PROJECT_ID)"
    exec opencode web
else
    echo "Opening OpenCode for: $PROJECT_NAME ($PROJECT_ID)"
    exec opencode
fi
