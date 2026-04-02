# Analyzed Projects - AI Agent Workspace

This directory provides the infrastructure for AI agents (OpenCode, Claude Code, Cursor, etc.) to analyze software project data loaded in the data-server.

## How It Works

1. **Upload & process** project data via the web UI (Git .iglog, GitHub JSON, JIRA JSON)
2. **Load** the project in the data-server (via web UI or API)
3. **Open your AI agent** in a project workspace folder and start asking questions

The AI agent reads context files describing the data model, then uses MCP tools to execute Python code against the loaded project graph.

## Architecture

```
User Question → AI Agent → MCP tool (execute_code) → data-server /execute → Results
```

The MCP server (`mcp-server/server.py`) is a thin bridge between the MCP protocol and the data-server HTTP API. The AI agent handles all code generation and conversation.

## Directory Structure

```
analyzed_projects/
├── mcp-server/          # MCP server wrapping data-server API
│   ├── server.py        # 4 tools: execute_code, generate_plot, get_project_status, load_project
│   └── requirements.txt
├── instructions/        # Context files describing the data model
│   ├── data-model.txt   # Graph structure + domain classes
│   ├── query-examples.txt  # Example Python queries
│   └── plot-patterns.txt   # Matplotlib patterns
├── projects/            # Per-project workspaces (gitignored)
│   └── {project-name}/
│       ├── README.md    # Auto-generated project info
│       ├── outputs/     # Saved plots and exports
│       └── scripts/     # User-saved analysis scripts
├── opencode.json        # OpenCode configuration
└── CLAUDE.md            # Claude Code context
```

## Quick Start

### Prerequisites

- Data-server running on port 8001
- A project loaded in the data-server
- Python with `mcp` and `httpx` packages installed

### Install MCP server dependencies

```bash
pip install -r analyzed_projects/mcp-server/requirements.txt
```

### Using with OpenCode

```bash
# From project root - automatically detects loaded project
./analyze.sh
```

### Using with Claude Code

Claude Code automatically picks up the `.mcp.json` config at the repo root.

```bash
claude
# The scriptbee-data MCP tools are available automatically
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `execute_code` | Execute Python code against the loaded project graph. Use `print()` for output. |
| `generate_plot` | Execute matplotlib code and save the resulting plot as JPEG. |
| `get_project_status` | Check which project is loaded and get statistics. |
| `load_project` | Load a project into the data-server by UUID. |
