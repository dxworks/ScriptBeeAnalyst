"""
ScriptBee Data MCP Server

Thin MCP bridge to the ScriptBee data-server API.
Exposes tools for executing Python code against loaded project graphs.

Usage (stdio):
    python server.py

Environment variables:
    DATA_SERVER_URL  - Data server base URL (default: http://localhost:8001)
    OUTPUTS_DIR      - Directory for saving plot images (default: ./outputs)
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

# Log to stderr only - stdout is reserved for MCP JSON-RPC protocol
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("scriptbee-mcp")

DATA_SERVER_URL = os.getenv("DATA_SERVER_URL", "http://localhost:8001")
OUTPUTS_DIR = Path(os.getenv("OUTPUTS_DIR", "./outputs"))
TIMEOUT = 60.0

mcp = FastMCP(
    "scriptbee-data",
    instructions=(
        "ScriptBee Data Server tools for querying software project analytics data. "
        "Use execute_code to run Python against the loaded project graph. "
        "Use generate_plot for matplotlib visualizations. "
        "Always check get_project_status first to see if a project is loaded."
    ),
)


@mcp.tool()
async def execute_code(code: str) -> str:
    """Execute Python code against the loaded project's in-memory graph.

    The code runs in a sandbox with `graph_data` dict available:
      - graph_data['git']    -> GitProject (commits, files, changes, authors)
      - graph_data['jira']   -> JiraProject (issues, statuses, types, users)
      - graph_data['github'] -> GitHubProject (pull requests, users, commits)

    Use print() to produce output - results come from captured stdout.
    Keep output concise (summarize, aggregate, limit to top N).

    Args:
        code: Python code to execute. Must use print() for output.

    Returns:
        The stdout output from execution, or an error message with traceback.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{DATA_SERVER_URL}/execute",
                json={"code": code},
            )
        except httpx.ConnectError:
            return f"Error: Cannot connect to data-server at {DATA_SERVER_URL}. Is it running?"

    if resp.status_code == 200:
        return resp.json().get("output", "(no output)")
    elif resp.status_code == 400:
        return f"Code execution error:\n{resp.json().get('error', 'Unknown error')}"
    else:
        return f"Unexpected response ({resp.status_code}): {resp.text}"


@mcp.tool()
async def generate_plot(code: str) -> str:
    """Execute Python code that generates a matplotlib plot.

    Same sandbox as execute_code, but also has `plt` (matplotlib.pyplot) available.
    Do NOT call plt.show() or plt.savefig() - the server captures the current figure.

    The resulting plot is saved as a JPEG file and the path is returned.

    Args:
        code: Python code that creates a matplotlib figure using plt.

    Returns:
        Path to the saved JPEG image, or an error message.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{DATA_SERVER_URL}/plot",
                json={"code": code},
            )
        except httpx.ConnectError:
            return f"Error: Cannot connect to data-server at {DATA_SERVER_URL}. Is it running?"

    if resp.status_code == 200:
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = OUTPUTS_DIR / f"plot_{timestamp}.jpg"
        filepath.write_bytes(resp.content)
        return f"Plot saved to: {filepath.resolve()}"
    elif resp.status_code == 400:
        return f"Plot generation error:\n{resp.json().get('error', 'Unknown error')}"
    else:
        return f"Unexpected response ({resp.status_code}): {resp.text}"


@mcp.tool()
async def get_project_status() -> str:
    """Check which project is currently loaded in the data server.

    Returns project ID, user ID, and statistics (commit count, issue count, PR count),
    or a message indicating no project is loaded.

    Call this first before running any queries to verify data is available.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.get(f"{DATA_SERVER_URL}/projects/current")
        except httpx.ConnectError:
            return f"Error: Cannot connect to data-server at {DATA_SERVER_URL}. Is it running?"

    if resp.status_code == 200:
        data = resp.json()
        stats = data.get("stats", {})
        return (
            f"Project loaded: {data.get('project_id')}\n"
            f"  Git commits: {stats.get('git_commits', 0)}\n"
            f"  JIRA issues: {stats.get('jira_issues', 0)}\n"
            f"  GitHub PRs:  {stats.get('github_prs', 0)}"
        )
    elif resp.status_code == 404:
        return "No project currently loaded. Use load_project with a project UUID."
    else:
        return f"Unexpected response ({resp.status_code}): {resp.text}"


@mcp.tool()
async def load_project(project_id: str) -> str:
    """Load a project into the data server's memory by its UUID.

    The project must exist in the database and have status 'ready' (already processed).
    Any previously loaded project will be unloaded first.

    Args:
        project_id: UUID of the project to load (shown in the web UI).

    Returns:
        Success message with project stats, or an error message.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{DATA_SERVER_URL}/projects/{project_id}/load",
            )
        except httpx.ConnectError:
            return f"Error: Cannot connect to data-server at {DATA_SERVER_URL}. Is it running?"

    if resp.status_code == 200:
        data = resp.json()
        stats = data.get("stats", {})
        return (
            f"Project '{data.get('project_name')}' loaded successfully.\n"
            f"  Git commits: {stats.get('git_commits', 0)}\n"
            f"  JIRA issues: {stats.get('jira_issues', 0)}\n"
            f"  GitHub PRs:  {stats.get('github_prs', 0)}"
        )
    elif resp.status_code == 404:
        error = resp.json().get("error", "Project not found")
        return f"Error: {error}"
    elif resp.status_code == 400:
        error = resp.json().get("error", "Bad request")
        return f"Error: {error}"
    else:
        return f"Unexpected response ({resp.status_code}): {resp.text}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
