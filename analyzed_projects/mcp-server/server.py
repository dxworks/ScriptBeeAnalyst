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

    The code runs in a sandbox where `graph_data` is an MCPSandboxView
    over the typed v2 Graph. Direct attribute access exposes:
      - graph_data.commits        -> CommitRegistry (.all(), .get(id), iter)
      - graph_data.issues         -> IssueRegistry
      - graph_data.pull_requests  -> PullRequestRegistry
      - graph_data.files          -> FileRegistry
      - graph_data.traits / .classifiers / .relations / .components
                                  -> the typed enrichment registries
      - graph_data.<anything else on the typed Graph> via read-through

    Pre-injected helpers (no import needed):
      - commit_issues(commit, graph_data)  -> list[Issue]
      - pr_commits(pr, graph_data)         -> list[Commit]
      - issue_commits(issue, graph_data)   -> list[Commit]
      - find_files_with_trait(trait_name)  -> list[File]
      - cochange_neighbors(file_id, window="lifetime", limit=None)
                                           -> list[File]
      - overview_as_dict(name)             -> dict | None (None for stubs)

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

    Same sandbox as execute_code (`graph_data` is an MCPSandboxView over
    the typed v2 Graph, plus the same pre-injected helpers:
    `commit_issues`, `pr_commits`, `issue_commits`, `find_files_with_trait`,
    `cochange_neighbors`, `overview_as_dict`), and additionally `plt`
    (matplotlib.pyplot). Do NOT call plt.show() or plt.savefig() — the
    server captures the current figure.

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


async def _execute_helper(snippet: str) -> str:
    """POST a one-line `graph_data.<helper>` call to `/execute`.

    Returns the raw stdout text (already JSON-stringified by the snippet)
    or a string starting with ``"__ERROR__:"`` on transport / sandbox
    failure. Per plan §1 D4 every MCP-facing helper is a method on
    `MCPSandboxView`; the four formerly-broken tools route through
    `/execute` rather than restoring the deleted `/enrichments/*` REST
    routes.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{DATA_SERVER_URL}/execute",
                json={"code": snippet},
            )
        except httpx.ConnectError:
            return f"__ERROR__:Cannot connect to data-server at {DATA_SERVER_URL}"

    if resp.status_code == 200:
        return resp.json().get("output", "")
    if resp.status_code == 400:
        return f"__ERROR__:{resp.json().get('error', 'Unknown error')}"
    return f"__ERROR__:HTTP {resp.status_code}: {resp.text}"


@mcp.tool()
async def list_metrics() -> dict:
    """List every registered Metric subclass with its emit-shape declaration.

    Calls `/execute` with `graph_data.list_metrics()` against
    `MCPSandboxView`. Source of truth: the running code — call this at
    the start of a session to discover what's available rather than
    relying on potentially stale documentation. Requires a project to
    be loaded.

    Returns a dict with keys:
      - `metrics`: [{name, family, emits_traits, emits_classifiers,
                     emits_relations, config_fields}]
      - `overviews`: [name, ...] — every registered overview table.
      - `counts`: per-category totals.
    """
    snippet = (
        "import json; "
        "print(json.dumps({"
        "'metrics': graph_data.list_metrics(), "
        "'overviews': graph_data.list_overviews(), "
        "'counts': {"
        "'metrics': len(graph_data.list_metrics()), "
        "'overviews': len(graph_data.list_overviews())"
        "}}))"
    )
    output = await _execute_helper(snippet)
    if output.startswith("__ERROR__:"):
        return {"error": output[len("__ERROR__:") :]}
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        return {"error": f"Sandbox response was not JSON: {e}; output={output[:200]!r}"}


@mcp.tool()
async def list_file_metrics(min_loc: float = 0.0, limit: int = 100) -> dict:
    """List per-file Lizard complexity metrics (LOC, max CCN, function count).

    Calls `/execute` with `graph_data.list_file_metrics(...)` against
    `MCPSandboxView`. Returns an empty list when no Lizard CSV was
    ingested for the loaded project.

    Args:
        min_loc: Filter out files with sum_nloc below this threshold.
        limit:  Cap on returned files (sandbox sorts by sum_nloc desc).

    Returns:
        Dict with `count` (pre-pagination total) and `files`
        (list of {file_path, source, sum_nloc, max_ccn, avg_ccn,
        function_count, longest_function_nloc}).
    """
    snippet = (
        f"import json; "
        f"print(json.dumps("
        f"graph_data.list_file_metrics(min_loc={min_loc!r}, limit={int(limit)})"
        f"))"
    )
    output = await _execute_helper(snippet)
    if output.startswith("__ERROR__:"):
        return {"error": output[len("__ERROR__:") :]}
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        return {"error": f"Sandbox response was not JSON: {e}; output={output[:200]!r}"}


@mcp.tool()
async def get_code_structure_summary() -> dict:
    """Counts of types/methods/fields/references from the CodeFrame (B2) layer.

    Calls `/execute` with `graph_data.code_structure_summary()` against
    `MCPSandboxView`. Returns `{"loaded": False, "source": None,
    "projects": []}` when no CodeFrame ingest happened. Use this
    to discover whether structural relations (calls.file-file,
    coupling.file-file, etc.) are available before querying them.
    """
    snippet = "import json; print(json.dumps(graph_data.code_structure_summary()))"
    output = await _execute_helper(snippet)
    if output.startswith("__ERROR__:"):
        return {"error": output[len("__ERROR__:") :]}
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        return {"error": f"Sandbox response was not JSON: {e}; output={output[:200]!r}"}


@mcp.tool()
async def get_duplication_summary() -> dict:
    """Counts of external/sibling/internal pairs from the DuDe (B3) layer.

    Calls `/execute` with `graph_data.duplication_summary()` against
    `MCPSandboxView`. Returns `{"loaded": False, "source": None,
    "projects": []}` when no DuDe ingest happened. Use this to discover
    whether duplication relations (`duplication.file-file.external`,
    `.sibling`, `.internal-summary`) are available before querying them.
    """
    snippet = "import json; print(json.dumps(graph_data.duplication_summary()))"
    output = await _execute_helper(snippet)
    if output.startswith("__ERROR__:"):
        return {"error": output[len("__ERROR__:") :]}
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        return {"error": f"Sandbox response was not JSON: {e}; output={output[:200]!r}"}


@mcp.tool()
async def get_quality_issues_summary() -> dict:
    """Counts of code-smell issues, distinct rules, top rules from Insider (B4).

    Calls `GET /enrichments/quality-issues/summary`. Returns
    `{"loaded": False, "source": None}` when no Insider ingest happened.
    Use this to discover whether `anomaly.codesmell.*` traits are available
    before calling `list_anomalies(trait_name="anomaly.codesmell.*")`.

    The `top_rules` field surfaces the most-fired rules by aggregate
    occurrence count (Insider's `value`); the `category_breakdown` field
    rolls those counts up by rule family (Inheritance / Traceability / ...).
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.get(
                f"{DATA_SERVER_URL}/enrichments/quality-issues/summary",
            )
        except httpx.ConnectError:
            return {"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}

    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text}"}
    return resp.json()


async def _resolve_current_project_id() -> str:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.get(f"{DATA_SERVER_URL}/projects/current")
        except httpx.ConnectError:
            return f"__ERROR__:Cannot connect to data-server at {DATA_SERVER_URL}"
    if resp.status_code != 200:
        return f"__ERROR__:HTTP {resp.status_code}: {resp.text}"
    # /projects/current always returns 200; the "not loaded" case is
    # carried by an empty project_id in the body.
    pid = resp.json().get("project_id")
    if not pid:
        return "__ERROR__:No project currently loaded. Use load_project first."
    return pid


def _filter_rule_auth_headers() -> dict:
    jwt = os.getenv("SCRIPTBEE_JWT") or os.getenv("DATA_SERVER_JWT")
    if jwt:
        return {"Authorization": f"Bearer {jwt}"}
    return {}


@mcp.tool()
async def create_filter_rule(
    name: str,
    nl_description: str,
    entity_kind: str,
    predicate: dict,
) -> str:
    """Create a project-scoped exclusion rule on the currently loaded project.

    The rule hides matched entities from every subsequent `graph_data.*`
    query. The unfiltered `graph_data_full` is unaffected. Ask the user
    any clarifying questions IN CHAT before calling this tool — the tool
    itself does not ask.

    Args:
        name: Short human label (e.g. "Tiny files (<20 LOC)").
        nl_description: The user's original phrasing, stored verbatim
            for audit/UI display.
        entity_kind: Lowercase EntityKind value. v1 supports:
            "file", "commit", "issue", "pull_request".
        predicate: A JSON dict matching the RuleDSL predicate. Either a
            single leaf `{"field": ..., "op": ..., "value": ...}` or a
            depth-1 conjunction `{"all_of": [<leaf>, <leaf>, ...]}`. The
            allowed (entity_kind, field) pairs in v1 are:
              file.loc, file.extension, file.path,
              commit.author_email, commit.message,
              issue.status, issue.type,
              pull_request.state, pull_request.author.
            Ops: lt | le | gt | ge | eq | ne | in | not_in | contains | regex.

    Returns:
        JSON string of the created rule on success, or `Error: ...` on failure.
    """
    pid = await _resolve_current_project_id()
    if pid.startswith("__ERROR__:"):
        return f"Error: {pid[len('__ERROR__:'):]}"

    body = {
        "name": name,
        "nl_description": nl_description,
        "dsl": {
            "entity_kind": entity_kind,
            "predicate": predicate,
        },
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{DATA_SERVER_URL}/projects/{pid}/rules",
                json=body,
                headers=_filter_rule_auth_headers(),
            )
        except httpx.ConnectError:
            return f"Error: Cannot connect to data-server at {DATA_SERVER_URL}. Is it running?"

    if resp.status_code == 200:
        return json.dumps(resp.json())
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {"error": resp.text}
    return f"Error ({resp.status_code}): {json.dumps(payload)}"


@mcp.tool()
async def list_filter_rules() -> str:
    """List active exclusion rules for the currently loaded project.

    Use this to answer "what filters are active?" or before creating a
    new rule to avoid duplicates. The unfiltered escape hatch
    `graph_data_full` ignores everything returned here.

    Returns:
        A short human-readable summary (one line per rule) plus a JSON
        block of the raw rules, or `Error: ...` on failure.
    """
    pid = await _resolve_current_project_id()
    if pid.startswith("__ERROR__:"):
        return f"Error: {pid[len('__ERROR__:'):]}"

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.get(
                f"{DATA_SERVER_URL}/projects/{pid}/rules",
                headers=_filter_rule_auth_headers(),
            )
        except httpx.ConnectError:
            return f"Error: Cannot connect to data-server at {DATA_SERVER_URL}. Is it running?"

    if resp.status_code != 200:
        return f"Error ({resp.status_code}): {resp.text}"

    data = resp.json()
    rules = data.get("rules", [])
    if not rules:
        return f"No filter rules active for project {pid}."

    lines = [f"{len(rules)} filter rule(s) active for project {pid}:"]
    for r in rules:
        lines.append(
            f"  - [{r.get('entity_kind')}] {r.get('name')}: "
            f"{r.get('nl_description')} (id={r.get('id')})"
        )
    lines.append("")
    lines.append(json.dumps({"rules": rules}))
    return "\n".join(lines)


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
