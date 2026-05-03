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
        "Call list_metrics() once per session to discover the live catalog of "
        "classifiers, traits, relations, and overview tables — the catalog reflects "
        "the source code, so every metric (including newly-added ones) is listed. "
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
      - graph_data['enrichments'] -> Enrichments (classifiers + traits + relations + overviews)

    Pre-injected helpers (no import needed):
      - find_files_with_trait(trait_name) -> list[str]
      - cochange_neighbors(file_id, window="lifetime", limit=10) -> list[tuple[str, float]]
      - overview_as_dict(name) -> dict

    Trait, classifier, overview, and relation names are project-versioned —
    call list_metrics() to get the live catalog (no hardcoded list here).

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

    Same sandbox as execute_code (graph_data, enrichments, find_files_with_trait,
    cochange_neighbors, overview_as_dict), but also has `plt` (matplotlib.pyplot)
    available. Do NOT call plt.show() or plt.savefig() - the server captures
    the current figure.

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
async def list_anomalies(
    trait_name: str | None = None,
    entity_kind: str | None = None,
) -> list[dict]:
    """List enrichment tags carrying anomaly traits.

    Calls `GET /enrichments/tags` and returns the parsed list. Filter by
    `trait_name` (e.g. "anomaly.testing.BugMagnet") and/or `entity_kind`
    (file | commit | author | issue | pr | component).

    Args:
        trait_name: Optional fully-qualified trait name to filter by.
        entity_kind: Optional entity kind to filter by.

    Returns:
        A list of tag dicts as returned by the data-server, or a single-item
        list with an `error` key on failure. Each tag has `entity_kind`,
        `entity_id`, `classifiers`, and `traits`.
    """
    params: dict = {}
    if trait_name is not None:
        params["trait"] = trait_name
    if entity_kind is not None:
        params["entity_kind"] = entity_kind

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.get(
                f"{DATA_SERVER_URL}/enrichments/tags",
                params=params,
            )
        except httpx.ConnectError:
            return [{"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}]

    if resp.status_code != 200:
        return [{"error": f"HTTP {resp.status_code}: {resp.text}"}]
    body = resp.json()
    return body.get("tags", [])


@mcp.tool()
async def get_overview_table(name: str) -> dict:
    """Fetch an overview table by name as a list-of-rows dict.

    Calls `GET /enrichments/overviews/{name}.csv`, parses the CSV, and returns
    `{"name": ..., "columns": [...], "rows": [{"entity_id": ..., col: value, ...}]}`.

    Call list_metrics() to get the current overview names — the catalog reflects
    source code, so it always includes the latest tables (e.g. `knowledge`,
    `nature`, `feature_traceability`, `pr_lifecycle` etc. as they're added).
    Each logical column expands to three CSV cells: `<col>_lifetime`,
    `<col>_recent`, `<col>_trend_percent` — preserved verbatim in the rows.

    Args:
        name: Overview table name.

    Returns:
        Dict with `name`, `columns` (logical column names from the header), and
        `rows` (list of row dicts). Returns `{"error": ...}` on failure.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.get(f"{DATA_SERVER_URL}/enrichments/overviews/{name}.csv")
        except httpx.ConnectError:
            return {"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}

    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text}"}

    import csv
    import io
    text = resp.text
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return {"name": name, "columns": [], "rows": []}

    # Strip the entity_id column and recover logical column names from the
    # `<col>_lifetime` cells (header layout is fixed by overview.writer).
    logical_cols: list[str] = []
    for h in header[1:]:
        if h.endswith("_lifetime"):
            logical_cols.append(h[: -len("_lifetime")])

    rows: list[dict] = []
    for record in reader:
        row: dict = {"entity_id": record[0]}
        for i, h in enumerate(header[1:], start=1):
            row[h] = record[i] if i < len(record) else ""
        rows.append(row)

    return {"name": name, "columns": logical_cols, "rows": rows}


@mcp.tool()
async def get_relation_edges(
    kind: str,
    window: str = "lifetime",
    limit: int = 100,
) -> list[dict]:
    """Fetch a relation file as a list of edges.

    Calls `GET /enrichments/relations/{kind}.csv?window={window}` and parses
    the 3-column CSV (`source,target,strength`).

    Call list_metrics() to get the current relation kinds — the catalog
    includes new kinds as they're added (e.g. `cochange.file-file.shared-devs`,
    `cochange.author-author.time-windowed`, `similarity.file-file.names`).

    Args:
        kind: Relation kind.
        window: `lifetime` or `recent` (some kinds have lifetime only).
        limit: Cap on returned edges; the server already sorts by strength desc.

    Returns:
        List of `{"source": str, "target": str, "strength": float}`, capped at
        `limit`. Returns `[{"error": ...}]` on failure.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.get(
                f"{DATA_SERVER_URL}/enrichments/relations/{kind}.csv",
                params={"window": window},
            )
        except httpx.ConnectError:
            return [{"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}]

    if resp.status_code != 200:
        return [{"error": f"HTTP {resp.status_code}: {resp.text}"}]

    import csv
    import io
    reader = csv.reader(io.StringIO(resp.text))
    next(reader, None)  # header
    edges: list[dict] = []
    for record in reader:
        if len(record) < 3:
            continue
        try:
            strength = float(record[2])
        except ValueError:
            strength = 0.0
        edges.append({"source": record[0], "target": record[1], "strength": strength})
        if len(edges) >= limit:
            break
    return edges


@mcp.tool()
async def list_metrics() -> dict:
    """List every classifier, anomaly trait, relation kind, and overview table.

    Reflects on the data-server's enrichment subpackages and returns the live
    catalog. Source of truth: the running code — call this at the start of a
    session to discover what's available rather than relying on potentially
    stale documentation. Does NOT require a project to be loaded.

    Returns a dict with keys:
      - `classifiers`: [{slot, entity, values, tagger, source_file, docstring}]
      - `traits`:      [{name, entity, family, tagger, source_file, docstring,
                         config_fields}]
      - `relations`:   [{kind, source_kind, target_kind, extractor, source_file,
                         docstring}]
      - `overviews`:   [{name, entity_kind, builder, source_file, docstring,
                         columns}]
      - `helpers`:     [{name, signature, purpose}] — sandbox helpers in execute_code
      - `source_roots`: dict pointing at the canonical code locations
      - `counts`:       per-category totals

    `source_file` paths are relative to data-server/. Read those files for
    the computational rule of any metric. `config_fields` (traits only) lists
    matching `EnrichmentConfig` fields where the thresholds live.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.get(f"{DATA_SERVER_URL}/enrichments/catalog")
        except httpx.ConnectError:
            return {"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}

    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text}"}
    return resp.json()


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
