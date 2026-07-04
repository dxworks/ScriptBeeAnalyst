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
from enum import Enum
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

# Log to stderr only - stdout is reserved for MCP JSON-RPC protocol
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("scriptbee-mcp")

DATA_SERVER_URL = os.getenv("DATA_SERVER_URL", "http://localhost:8001")
OUTPUTS_DIR = Path(os.getenv("OUTPUTS_DIR", "./outputs"))
TIMEOUT = 60.0

# Sentinel returned by ``get_project_merge_state`` when the data-server
# is unreachable or no project is loaded — distinct from the two real
# lifecycle states so the gating helper can produce a clear error.
_NO_PROJECT = "NO_PROJECT"

mcp = FastMCP(
    "scriptbee-data",
    instructions=(
        "ScriptBee Data Server. Projects have two lifecycle stages: "
        "PRE_MERGE (setup — author matching, enrichment thresholds, filter rules) "
        "and FINALIZED (query — code execution, plots, metrics, overviews). "
        "Always call get_project_status FIRST to learn the current stage; "
        "the tool surface differs per stage. See instructions/setup.md for "
        "PRE_MERGE guidance and instructions/compass.md for FINALIZED."
    ),
)


# ---------------------------------------------------------------------------
# Lifecycle-state gating (UnifiedUsers redesign §I / task P5.B)
#
# Every tool below declares which stage it is callable in. The gate is
# checked at the top of the tool body by calling ``_require_state``. We
# deliberately refetch the merge_state per call (no caching) — the cost
# is one cheap GET against ``/projects/current`` and the explicit
# refresh avoids stale-state foot-guns when the agent finalises mid-run.
# ---------------------------------------------------------------------------


class _Stage(str, Enum):
    PRE_MERGE = "PRE_MERGE"
    FINALIZED = "FINALIZED"


async def get_project_merge_state() -> str:
    """Fetch the current project's ``merge_state`` from the data-server.

    Returns one of ``"PRE_MERGE"`` / ``"FINALIZED"`` / ``"NO_PROJECT"``.
    A transport failure also surfaces as ``"NO_PROJECT"`` so the gating
    helper can produce a single, uniform "load a project first" message
    rather than a stack trace.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.get(f"{DATA_SERVER_URL}/projects/current")
        except httpx.ConnectError:
            return _NO_PROJECT
    if resp.status_code != 200:
        return _NO_PROJECT
    body = resp.json()
    if not body.get("loaded"):
        return _NO_PROJECT
    # The data-server surfaces ``merge_state`` on the loaded body (P5.B
    # change). Fall through to ``PRE_MERGE`` if an older data-server
    # build is talking back — keeps the MCP forward-compatible.
    return body.get("merge_state") or _Stage.PRE_MERGE.value


def _recovery_hint(current: str, expected: _Stage) -> str:
    if current == _NO_PROJECT:
        return "Load a project first with load_project(project_id=...)."
    if current == _Stage.PRE_MERGE.value and expected == _Stage.FINALIZED:
        return "Call finalize_project() to switch the project into query mode."
    if current == _Stage.FINALIZED.value and expected == _Stage.PRE_MERGE:
        return "The project is finalized; re-import to redo setup."
    return ""


async def _require_state(expected: _Stage) -> None:
    """Refuse the call if the current project's ``merge_state`` doesn't match.

    Raises :class:`McpError` with a recovery-hint message the LLM can
    use to decide its next tool call (load_project / finalize_project /
    re-import).
    """
    current = await get_project_merge_state()
    if current != expected.value:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=(
                    f"This tool requires merge_state={expected.value}, "
                    f"but the project is in {current}. "
                    f"{_recovery_hint(current, expected)}"
                ).strip(),
            )
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
    await _require_state(_Stage.FINALIZED)
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
    await _require_state(_Stage.FINALIZED)
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
        # ``/projects/current`` always returns 200; the no-load case is
        # ``loaded=False`` in the body (per data-server behaviour).
        if not data.get("loaded"):
            return "No project currently loaded. Use load_project with a project UUID."
        stats = data.get("stats", {})
        merge_state = data.get("merge_state", "PRE_MERGE")
        return (
            f"Project loaded: {data.get('project_id')}\n"
            f"  merge_state: {merge_state}\n"
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
    """List enrichment traits (anomalies) carrying their target + evidence.

    Routes through `/execute` against `graph_data.traits` (the legacy
    `GET /enrichments/tags` REST route was deleted in the v2 refactor).
    Filter by `trait_name` and/or `entity_kind`.

    Args:
        trait_name: Optional trait name to filter by. Exact match (e.g.
            "anomaly.testing.BugMagnet"), or a `*`-suffixed prefix
            (e.g. "anomaly.codesmell.*") to match a whole family.
        entity_kind: Optional target kind to filter by — an EntityKind
            value (file | commit | issue | pull_request | component | ...).
            The aliases `pr` and `author` are accepted too.

    Returns:
        A list of trait dicts, each with `name`, `family`, `severity`,
        `is_proxy`, `entity_kind`, `entity_id`, and `evidence`. On failure,
        a single-item list with an `error` key.
    """
    await _require_state(_Stage.FINALIZED)
    snippet = f"""
import json
_tn = {trait_name!r}
_ek = {entity_kind!r}
if _ek is not None:
    _ek = {{"pr": "pull_request", "author": "unified_user"}}.get(_ek, _ek)
if _tn is None:
    _traits = list(graph_data.traits)
elif _tn.endswith("*"):
    _pref = _tn[:-1]
    _traits = [t for t in graph_data.traits if t.name.startswith(_pref)]
else:
    _traits = list(graph_data.traits.of_name(_tn))
_out = []
for _t in _traits:
    _k = _t.target.kind.value
    if _ek is not None and _k != _ek:
        continue
    _out.append({{
        "name": _t.name,
        "family": getattr(_t.family, "value", _t.family),
        "severity": _t.severity,
        "is_proxy": _t.is_proxy,
        "entity_kind": _k,
        "entity_id": _t.target.id,
        "evidence": _t.evidence,
    }})
print(json.dumps(_out, default=str))
"""
    output = await _execute_helper(snippet)
    if output.startswith("__ERROR__:"):
        return [{"error": output[len("__ERROR__:") :]}]
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        return [{"error": f"Sandbox response was not JSON: {e}; output={output[:200]!r}"}]


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
    await _require_state(_Stage.FINALIZED)
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
    await _require_state(_Stage.FINALIZED)
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
    await _require_state(_Stage.FINALIZED)
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
    await _require_state(_Stage.FINALIZED)
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
    await _require_state(_Stage.FINALIZED)
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
    await _require_state(_Stage.FINALIZED)
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
    await _require_state(_Stage.FINALIZED)
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


# ---------------------------------------------------------------------------
# PRE_MERGE-only tools (UnifiedUsers redesign §I, task P5.B).
#
# Thin wrappers around existing data-server endpoints. Each gate refuses
# the call if the project is already FINALIZED — re-import is the only
# way back into setup. Tools whose data-server endpoint does NOT exist
# yet are stubbed with a precise error message naming the missing route;
# they are flagged for the P6 follow-up.
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_author_suggestions(min_score: float = 0.0) -> list[dict]:
    """List smart-merge author suggestions for the loaded project.

    Wraps ``GET /projects/{id}/authors/suggestions`` on the data-server.
    Each suggestion clusters identities (GitAccount / JiraUser /
    GitHubUser) that are likely the same person; apply them with
    ``apply_author_merge`` or reject with ``reject_author_pair``.

    Args:
        min_score: Filter out suggestions with a confidence below this
            threshold (the data-server scoring is in [0.0, 1.0]).

    Returns:
        List of suggestion dicts as returned by the data-server (each
        with ``suggestion_id``, ``identities``, ``confidence``, ...).
        Returns ``[{"error": ...}]`` on transport / server failure.
    """
    await _require_state(_Stage.PRE_MERGE)

    pid = await _resolve_current_project_id()
    if pid.startswith("__ERROR__:"):
        return [{"error": pid[len("__ERROR__:"):]}]

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.get(
                f"{DATA_SERVER_URL}/projects/{pid}/authors/suggestions",
            )
        except httpx.ConnectError:
            return [{"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}]

    if resp.status_code != 200:
        return [{"error": f"HTTP {resp.status_code}: {resp.text}"}]

    body = resp.json()
    suggestions = body.get("suggestions", [])
    if min_score > 0.0:
        suggestions = [
            s for s in suggestions
            if float(s.get("confidence", 0.0)) >= min_score
        ]
    return suggestions


@mcp.tool()
async def apply_author_merge(
    suggestion_id: str,
    display_name: str,
    primary_email: str | None = None,
) -> dict:
    """Apply a smart-merge author suggestion.

    Wraps ``POST /projects/{id}/authors/suggestions/apply``. Creates a
    UnifiedUser from EVERY identity in the suggestion (no per-identity
    deselection — call ``list_author_suggestions`` again afterwards to
    see what's left). To do partial merges, use the web UI.

    Args:
        suggestion_id: id returned by ``list_author_suggestions``.
        display_name: Canonical name to attach to the new UnifiedUser.
        primary_email: Optional canonical email (None falls through to
            the suggestion's default).

    Returns:
        The created UnifiedUser dict on success, or ``{"error": ...}``.
    """
    await _require_state(_Stage.PRE_MERGE)

    pid = await _resolve_current_project_id()
    if pid.startswith("__ERROR__:"):
        return {"error": pid[len("__ERROR__:"):]}

    # We have to re-fetch the suggestion to know which identity keys it
    # contains, because the server's /apply endpoint expects the keys
    # explicitly. The suggestions cache is keyed by suggestion_id on
    # the server side.
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            list_resp = await client.get(
                f"{DATA_SERVER_URL}/projects/{pid}/authors/suggestions",
            )
        except httpx.ConnectError:
            return {"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}
    if list_resp.status_code != 200:
        return {"error": f"Could not refresh suggestions (HTTP {list_resp.status_code})"}
    suggestions = list_resp.json().get("suggestions", [])
    target = next(
        (s for s in suggestions if s.get("suggestion_id") == suggestion_id),
        None,
    )
    if target is None:
        return {"error": f"Suggestion {suggestion_id!r} not found in current cache."}

    # Identity dicts ship `source` + `source_key`; the globally-unique
    # key the server's apply endpoint expects is "<source>:<source_key>"
    # (see SourceIdentity.key on the data-server).
    selected_keys = [
        f"{i.get('source')}:{i.get('source_key')}"
        for i in target.get("identities", [])
        if i.get("source") and i.get("source_key")
    ]
    if len(selected_keys) < 2:
        return {"error": "Suggestion has fewer than 2 identities; nothing to merge."}

    payload = {
        "suggestion_id": suggestion_id,
        "selected_identity_keys": selected_keys,
        "unselected_identity_keys": [],
        "name": display_name,
        "email": primary_email or "unknown@unknown",
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{DATA_SERVER_URL}/projects/{pid}/authors/suggestions/apply",
                json=payload,
            )
        except httpx.ConnectError:
            return {"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}

    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text}"}
    return resp.json()


@mcp.tool()
async def reject_author_pair(pair_id: str) -> dict:
    """Reject a smart-merge author suggestion.

    Wraps ``POST /projects/{id}/authors/suggestions/reject``. ``pair_id``
    is the suggestion_id from ``list_author_suggestions`` — every
    pairwise combination of identities in that suggestion is recorded
    as rejected so the suggestion does not reappear on future scans.

    Returns:
        The data-server response dict, or ``{"error": ...}``.
    """
    await _require_state(_Stage.PRE_MERGE)

    pid = await _resolve_current_project_id()
    if pid.startswith("__ERROR__:"):
        return {"error": pid[len("__ERROR__:"):]}

    # Re-fetch to extract identity_keys; the reject endpoint expects them.
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            list_resp = await client.get(
                f"{DATA_SERVER_URL}/projects/{pid}/authors/suggestions",
            )
        except httpx.ConnectError:
            return {"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}
    if list_resp.status_code != 200:
        return {"error": f"Could not refresh suggestions (HTTP {list_resp.status_code})"}
    suggestions = list_resp.json().get("suggestions", [])
    target = next(
        (s for s in suggestions if s.get("suggestion_id") == pair_id),
        None,
    )
    if target is None:
        return {"error": f"Suggestion {pair_id!r} not found in current cache."}

    identity_keys = [
        f"{i.get('source')}:{i.get('source_key')}"
        for i in target.get("identities", [])
        if i.get("source") and i.get("source_key")
    ]
    if len(identity_keys) < 2:
        return {"error": "Suggestion has fewer than 2 identities; nothing to reject."}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{DATA_SERVER_URL}/projects/{pid}/authors/suggestions/reject",
                json={"identity_keys": identity_keys},
            )
        except httpx.ConnectError:
            return {"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}

    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text}"}
    return resp.json()


@mcp.tool()
async def unmerge(unified_user_id: str) -> dict:
    """Undo a smart-merge by deleting a UnifiedUser.

    STUB — the MCP wrapper is in place but the dedicated unmerge
    endpoint does NOT exist yet. The closest data-server route is
    ``DELETE /projects/{id}/authors/users/{unified_user_id}`` which
    deletes the user mapping outright; calling it from the MCP layer
    today would leave the surrounding state in an inconsistent shape
    (the graph-side mirror, rejected-pair history, etc. are not
    handled). Flagged for P6.

    Recovery in the meantime: from the web UI, the operator can call
    ``DELETE /projects/{id}/authors/users`` (delete-all) and re-run
    author matching, or re-import the project.

    Args:
        unified_user_id: The UU id to unmerge.

    Returns:
        Always an error dict — this tool never succeeds in P5.B.
    """
    await _require_state(_Stage.PRE_MERGE)
    return {
        "error": (
            "unmerge is not yet implemented in the MCP layer. The "
            "data-server has a delete endpoint at "
            "DELETE /projects/{id}/authors/users/{unified_user_id} but it "
            "does not yet handle the full unmerge contract (rejected-pair "
            "history, graph mirror cleanup). Reset author matching from the "
            "web UI or re-import the project. Flagged for task P6."
        )
    }


@mcp.tool()
async def get_enrichment_config() -> dict:
    """Read the per-project enrichment-config overrides.

    Wraps ``GET /projects/{id}/config-overrides`` on the data-server.
    Returns the bundled catalogue (every editable field with its
    default + current effective value) and the persisted overrides
    dict. Use ``set_enrichment_threshold`` to change a single value.

    Returns:
        Dict with ``catalogue``, ``overrides``, and ``updated_at``; or
        ``{"error": ...}`` on failure.
    """
    await _require_state(_Stage.PRE_MERGE)

    pid = await _resolve_current_project_id()
    if pid.startswith("__ERROR__:"):
        return {"error": pid[len("__ERROR__:"):]}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.get(
                f"{DATA_SERVER_URL}/projects/{pid}/config-overrides",
            )
        except httpx.ConnectError:
            return {"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}

    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text}"}
    return resp.json()


@mcp.tool()
async def set_enrichment_threshold(key: str, value) -> dict:  # noqa: ANN001
    """Set a single per-project enrichment-config override.

    Wraps ``PUT /projects/{id}/config-overrides``. The PUT endpoint
    REPLACES the entire overrides dict, so this tool reads the existing
    overrides first, sets / overrides ``key``, then writes back.

    Args:
        key: An editable EnrichmentConfig field name (see
            ``get_enrichment_config().catalogue`` for the live list).
        value: The new value. Scalars (int / float / str / bool) are
            sent as-is. Composite shapes (lists of buckets, regex maps)
            must already be in the catalogue's storage shape.

    Returns:
        The updated row envelope (``{"overrides": ..., "updated_at": ...}``)
        on success, or ``{"error": ...}``.
    """
    await _require_state(_Stage.PRE_MERGE)

    pid = await _resolve_current_project_id()
    if pid.startswith("__ERROR__:"):
        return {"error": pid[len("__ERROR__:"):]}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            get_resp = await client.get(
                f"{DATA_SERVER_URL}/projects/{pid}/config-overrides",
            )
        except httpx.ConnectError:
            return {"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}
    if get_resp.status_code != 200:
        return {"error": f"Could not read current overrides (HTTP {get_resp.status_code})"}

    overrides = dict(get_resp.json().get("overrides") or {})
    overrides[key] = value

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            put_resp = await client.put(
                f"{DATA_SERVER_URL}/projects/{pid}/config-overrides",
                json={"overrides": overrides},
            )
        except httpx.ConnectError:
            return {"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}

    if put_resp.status_code != 200:
        return {"error": f"HTTP {put_resp.status_code}: {put_resp.text}"}
    return put_resp.json()


@mcp.tool()
async def finalize_project() -> dict:
    """Finalize the loaded project: PRE_MERGE → FINALIZED.

    Wraps ``POST /projects/{id}/finalize``. One-way transition:
    auto-creates singleton UnifiedUsers for orphan accounts, rewrites
    every role-typed account ref to target UNIFIED_USER, re-runs the
    people-side enrichment phase, and persists the new state. After
    this call, the PRE_MERGE-only tools (author matching, enrichment
    config, finalize itself) are refused, and the FINALIZED-only
    exploration tools (execute_code, generate_plot, list_metrics, ...)
    become available.

    To redo author matching or enrichment thresholds after finalize,
    re-import the project — there is no inverse.

    Returns:
        The data-server's finalize summary on success
        (``merge_state``, ``unified_users_created``, ``refs_rewritten``,
        ``phase_b_relations_built``, ``duration_ms``, ...), or
        ``{"error": ...}``.
    """
    await _require_state(_Stage.PRE_MERGE)

    pid = await _resolve_current_project_id()
    if pid.startswith("__ERROR__:"):
        return {"error": pid[len("__ERROR__:"):]}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{DATA_SERVER_URL}/projects/{pid}/finalize",
            )
        except httpx.ConnectError:
            return {"error": f"Cannot connect to data-server at {DATA_SERVER_URL}"}

    if resp.status_code != 200:
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {"error": resp.text}
        return {"error": f"HTTP {resp.status_code}: {json.dumps(payload)}"}
    return resp.json()


if __name__ == "__main__":
    mcp.run(transport="stdio")
