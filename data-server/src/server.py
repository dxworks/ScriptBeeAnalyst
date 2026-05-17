import asyncio
import io
import sys
import traceback
import logging
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.responses import JSONResponse, Response
from contextlib import asynccontextmanager
import matplotlib.pyplot as plt

import re
from datetime import datetime

from supabase import create_client, Client
from src.config import SUPABASE_URL, SUPABASE_SERVICE_KEY, WORKSPACE_ROOT
from src.logger import get_logger
# Chunk 19: smart-merge moved off the legacy ``graph_data: dict`` global.
# ``SourceIdentity`` + ``UnifiedUser`` now live in ``src.smart_merge.identity``
# (per D5 — internal smart-merge DTOs), and identities are derived from the
# typed v2 :class:`Graph` via :mod:`src.smart_merge.identity_extractor`.
from src.smart_merge.engine import AuthorSmartMergeEngine
from src.smart_merge.identity import SourceIdentity, UnifiedUser
from src.smart_merge.identity_extractor import extract_all_identities
from src.smart_merge.state_store import smart_merge_state_store
from src.smart_merge.supabase_repository import SupabaseSmartMergeRepository
from src.smart_merge.types import (
    MAX_IDENTITIES_PER_SUGGESTION,
    RejectedPair,
    Suggestion,
    UserMapping,
)
# The v2 pipeline (``run_pipeline``) writes directly into the typed
# ``Graph`` (``graph.relations`` / ``graph.traits`` / ``graph.classifiers``).
# The legacy ``compute_enrichments`` / ``Enrichments`` /
# ``SupabaseEnrichmentRepository`` stack was deleted in Chunk 10 along
# with the rest of the pre-refactor enrichment layer.
from src.enrichment.pipeline import PipelineResult, run_pipeline
from src.graph_store import graph_store
from src.common.kernel import Graph
from src.common.pickle_store import PickleStore
from src import processor as v2_processor
from src.sandbox import (
    MCPSandboxView,
    commit_issues as _sandbox_commit_issues,
    issue_commits as _sandbox_issue_commits,
    pr_commits as _sandbox_pr_commits,
)

logger = get_logger("data-server")


# Filter to suppress Uvicorn access logs for /projects/current endpoint
class SuppressCurrentProjectLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Suppress logs that contain "GET /projects/current"
        return "GET /projects/current" not in record.getMessage()


# Apply filter to Uvicorn's access logger
logging.getLogger("uvicorn.access").addFilter(SuppressCurrentProjectLogFilter())


class CodeRequest(BaseModel):
    """Request body for code execution endpoints."""
    code: str


class ApplySuggestionRequest(BaseModel):
    """Request body for applying a merge suggestion."""
    suggestion_id: str
    selected_identity_keys: List[str]
    unselected_identity_keys: List[str] = []
    name: str
    email: str


class RejectSuggestionRequest(BaseModel):
    """Request body for rejecting a merge suggestion."""
    identity_keys: List[str]


def load_graph_v2_from_disk(project_id: str) -> Optional[Graph]:
    """Lazily load a typed v2 :class:`Graph` from the local pickle store.

    Chunk 8: every typed registry lives under
    ``/tmp/pickles/<project_id>/`` as a per-file pickle (see
    :class:`~src.common.pickle_store.PickleStore`). Returns ``None`` if
    no on-disk meta.json exists for this project — let the caller fall
    through to a build trigger or surface a "not built" message.
    """
    from src.processor import _project_pickle_dir

    base_dir = _project_pickle_dir(project_id)
    if not (base_dir / PickleStore.META_FILENAME).exists():
        return None
    try:
        return Graph.lazy(project_id, PickleStore(base_dir))
    except Exception as exc:  # noqa: BLE001
        # Per the greenfield rule: bumped schema_version / partial
        # layout differences surface here. Logging + None signals "user
        # needs to rebuild" without crashing the load endpoint.
        logger.warning(
            f"Graph.lazy failed for {project_id}: {type(exc).__name__}: {exc}"
        )
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager — server starts with no data loaded.

    Chunk 19: the legacy ``graph_data: dict`` global is gone. The v2
    typed :class:`Graph` lives in ``graph_store``; smart-merge state
    lives in ``smart_merge_state_store``. Both are wiped on shutdown.
    """
    global current_project_id, current_user_id
    logger.info("Data server started on port 8001")

    try:
        yield
    finally:
        graph_store.clear()
        smart_merge_state_store.clear()
        current_project_id = None
        current_user_id = None
        logger.info("Shutdown complete - graph cleared from memory")


# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(
    title="ScriptBeeAssistant Data Server",
    description="""
## Overview

FastAPI backend for ScriptBeeAssistant. Loads typed v2 :class:`Graph`
instances per `project_id` via the ``/projects/{id}/build`` flow and
exposes query endpoints over them.

**No authentication required** — this is a standalone development server.

## Graph Data Structure

The `graph_data` variable available in /execute and /plot endpoints is
an :class:`MCPSandboxView` wrapping the loaded typed :class:`Graph`. Read
typed registries directly:

```python
print(len(graph_data.commits))
print(graph_data.git_accounts.all())
print(graph_data.issues.by_assignee[user_ref])
```

## Workflow

1. Upload serialized files via web UI
2. POST `/projects/{id}/build` — builds the typed Graph, runs the pipeline
3. POST `/execute` — run Python queries against the loaded Graph
4. POST `/plot` — same shape, returns matplotlib JPEG
5. DELETE `/projects/{id}/unload` — drop from memory

Smart-merge endpoints (`/projects/{id}/authors/*`) consume the loaded
typed Graph, derive `SourceIdentity` instances from
`graph.git_accounts` / `graph.jira_users` / `graph.github_users`, and
persist `UnifiedUser` records into Supabase.
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Add CORS middleware to allow requests from web UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",  # Angular dev server
        "http://127.0.0.1:4200",  # Alternative localhost
    ],
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, DELETE, etc.)
    allow_headers=["*"],  # Allow all headers
)


# =============================================================================
# Endpoints
# =============================================================================

# Track currently loaded project (kept module-level for the /execute /
# /plot handlers' "which project's Graph do I expose?" lookup, and for
# /projects/current). Set on /load and /build; cleared on /unload and
# shutdown.
current_project_id = None
current_project_name = None
current_user_id = None


def _stats_for(graph: Optional[Graph], state) -> dict:
    """Build the per-project stats block used by /health and /projects/current.

    Reads directly off the typed Graph registries; the per-source counts
    are the same shape the legacy ``graph_data``-backed endpoints
    surfaced. ``unified_users`` counts the in-memory smart-merge
    :class:`UnifiedUser` list (replayed from Supabase on /load).
    """
    if graph is None:
        return {
            "git_commits": 0,
            "jira_issues": 0,
            "github_prs": 0,
            "unified_users": 0,
        }
    return {
        "git_commits": len(graph.commits),
        "jira_issues": len(graph.issues),
        "github_prs": len(graph.pull_requests),
        "unified_users": len(state.users) if state is not None else 0,
    }


@app.get("/health")
async def health_check():
    """Health check endpoint.

    Chunk 19: ``data_loaded`` now reflects ``graph_store`` (the typed-v2
    home), and ``stats`` is computed off the loaded Graph rather than
    the deleted ``graph_data`` dict.
    """
    graph = (
        graph_store.get(current_project_id) if current_project_id else None
    )
    state = (
        smart_merge_state_store.get(current_project_id)
        if current_project_id else None
    )
    return {
        "status": "ok",
        "mode": "standalone",
        "data_loaded": graph is not None,
        "current_project_id": current_project_id,
        "loaded_projects": graph_store.get_all_project_ids(),
        "stats": _stats_for(graph, state),
    }


@app.get("/projects/current")
async def get_current_project():
    """Get information about the currently loaded project."""
    graph = (
        graph_store.get(current_project_id) if current_project_id else None
    )
    if graph is None or not current_project_id:
        return JSONResponse(
            {"message": "No project currently loaded"},
            status_code=404
        )
    state = smart_merge_state_store.get(current_project_id)
    return {
        "project_id": current_project_id,
        "project_name": current_project_name,
        "user_id": current_user_id,
        "stats": _stats_for(graph, state),
    }


@app.post("/projects/{project_id}/load")
async def load_project(project_id: str):
    """Load a previously-built v2 :class:`Graph` from the local pickle store.

    Chunk 19 rewrite: the v1 pickle-pull from Supabase Storage is gone —
    the v2 build path (``/projects/{id}/build`` → ``Graph.dump`` to
    ``/tmp/pickles/<project_id>/``) is the only source of truth for the
    typed graph.

    Reads:
    * Project metadata (name, status, user_id) from the ``projects``
      table.
    * The typed Graph via :func:`load_graph_v2_from_disk` (or returns
      404 if no on-disk pickle exists).

    Side effects:
    * Sets ``graph_store[project_id]``.
    * Updates ``current_project_id`` / ``current_project_name`` /
      ``current_user_id``.
    * Replays persisted user mappings into
      ``smart_merge_state_store[project_id]``.
    """
    global current_project_id, current_project_name, current_user_id

    logger.info(f"Loading project: {project_id}")

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    try:
        response = await asyncio.to_thread(
            lambda: supabase.table("projects").select("*").eq("id", project_id).single().execute()
        )
        project = response.data
        if not project:
            return JSONResponse(
                {"error": f"Project {project_id} not found"}, status_code=404
            )

        user_id = project["user_id"]
        project_name = project["name"]
        project_status = project["status"]

        if project_status != "ready":
            return JSONResponse(
                {"error": (
                    f"Project is not ready (status: {project_status}). "
                    "Please process the project first."
                )},
                status_code=400,
            )
    except Exception as e:
        logger.error(f"Failed to fetch project from database: {e}")
        return JSONResponse(
            {"error": f"Failed to fetch project: {str(e)}"}, status_code=500
        )

    # Drop any previously-loaded project's typed Graph + smart-merge cache.
    if current_project_id and current_project_id != project_id:
        graph_store.delete(current_project_id)
        smart_merge_state_store.delete(current_project_id)

    # Pull the typed Graph from the local pickle store.
    graph = await asyncio.to_thread(load_graph_v2_from_disk, project_id)
    if graph is None:
        return JSONResponse(
            {"error": (
                f"No built graph found for project {project_id}. "
                "Run `Build Graph` first."
            )},
            status_code=404,
        )

    graph_store.set(project_id, graph)
    smart_merge_state_store.reset(project_id)

    current_project_id = project_id
    current_project_name = project_name
    current_user_id = user_id

    # Auto-replay persisted user mappings onto the loaded graph.
    replay_result = {"users_replayed": 0, "identities_matched": 0, "identities_missing": 0}
    try:
        replay_result = _replay_user_mappings(project_id)
    except Exception as e:
        logger.warning(f"Failed to replay user mappings (non-fatal): {e}")

    state = smart_merge_state_store.get(project_id)
    logger.info(f"Project {project_id} loaded into memory")

    return {
        "message": "Project loaded successfully",
        "project_id": project_id,
        "project_name": project_name,
        "user_id": user_id,
        "stats": _stats_for(graph, state),
        "replay": replay_result,
    }


@app.post("/projects/{project_id}/build")
async def build_project(project_id: str):
    """Build a typed v2 :class:`Graph` for ``project_id``.

    Chunk 8: drives the new processor end-to-end —
    download → per-source :class:`Transformer` → typed Graph →
    ``run_pipeline`` → persist. The freshly built Graph lands in
    :data:`graph_store` keyed by ``project_id``.
    """
    global current_project_id

    try:
        graph, pipeline_result = await asyncio.to_thread(
            v2_processor.build_graph, project_id
        )
    except NotImplementedError as exc:
        logger.warning(f"build_graph deferred: {exc}")
        return JSONResponse(
            {"error": str(exc), "deferred": True}, status_code=501,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_graph failed")
        return JSONResponse(
            {"error": f"build_graph failed: {exc}"}, status_code=500,
        )

    graph_store.set(project_id, graph)
    # Fresh build invalidates any cached smart-merge state for this project.
    smart_merge_state_store.reset(project_id)

    # Update the "currently loaded" pointers so /execute against this id
    # works without an explicit /load round-trip.
    current_project_id = project_id

    return {
        "project_id": project_id,
        "schema_version": graph.schema_version,
        "built_at": graph.built_at.isoformat(),
        "pipeline": {
            "builders_run": pipeline_result.builders_run,
            "metrics_run": pipeline_result.metrics_run,
            "traits_emitted": pipeline_result.traits_emitted,
            "classifiers_emitted": pipeline_result.classifiers_emitted,
            "relations_emitted": pipeline_result.relations_emitted,
            "errors": [e.model_dump() for e in pipeline_result.errors],
        },
    }


@app.delete("/projects/{project_id}/unload")
async def unload_project(project_id: str):
    """Unload a project's typed Graph + smart-merge state from memory.

    Chunk 19: drops both ``graph_store[project_id]`` AND
    ``smart_merge_state_store[project_id]``.
    """
    global current_project_id, current_project_name, current_user_id

    removed_graph = graph_store.delete(project_id)
    smart_merge_state_store.delete(project_id)

    if current_project_id == project_id:
        current_project_id = None
        current_project_name = None
        current_user_id = None

    if not removed_graph:
        return JSONResponse(
            {"error": f"Project {project_id} is not currently loaded"},
            status_code=400,
        )

    logger.info(f"Project {project_id} unloaded successfully")
    return {"message": "Project unloaded successfully"}


@app.post("/projects/{project_id}/scaffold-workspace")
async def scaffold_workspace(project_id: str):
    """Create the per-project workspace folder for AI agent analysis.

    Creates a directory under WORKSPACE_ROOT with a README containing
    project info, stats, and UUID. Also creates outputs/ and scripts/
    subdirs.
    """
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    try:
        response = supabase.table("projects").select("name").eq("id", project_id).single().execute()
        project = response.data
        if not project:
            return JSONResponse({"error": f"Project {project_id} not found"}, status_code=404)
        project_name = project["name"]
    except Exception as e:
        logger.error(f"Failed to fetch project for scaffolding: {e}")
        return JSONResponse({"error": f"Failed to fetch project: {str(e)}"}, status_code=500)

    # Sanitize name for directory
    folder_name = re.sub(r'[^a-z0-9-]', '', project_name.lower().replace(' ', '-'))
    if not folder_name:
        folder_name = project_id[:8]

    workspace_path = Path(WORKSPACE_ROOT) / folder_name

    workspace_path.mkdir(parents=True, exist_ok=True)
    (workspace_path / "outputs").mkdir(exist_ok=True)
    (workspace_path / "scripts").mkdir(exist_ok=True)

    # Gather stats if project is loaded
    stats_text = ""
    graph = graph_store.get(project_id)
    if graph is not None:
        stats_text = (
            f"\n## Statistics\n\n"
            f"- Git commits: {len(graph.commits)}\n"
            f"- JIRA issues: {len(graph.issues)}\n"
            f"- GitHub PRs: {len(graph.pull_requests)}\n"
        )

    readme_content = (
        f"# {project_name}\n\n"
        f"- **Project UUID:** `{project_id}`\n"
        f"- **Workspace created:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{stats_text}\n"
        f"## Usage\n\n"
        f"Open your AI agent in this directory to analyze this project:\n\n"
        f"```bash\n"
        f"cd analyzed_projects/projects/{folder_name}\n"
        f"opencode   # or: claude\n"
        f"```\n\n"
        f"The agent has MCP tools to query the data-server.\n"
        f"See `analyzed_projects/instructions/` for data model documentation.\n"
    )
    (workspace_path / "README.md").write_text(readme_content)

    relative_path = f"analyzed_projects/projects/{folder_name}"
    logger.info(f"Workspace scaffolded for project '{project_name}' at {relative_path}")

    return {
        "path": relative_path,
        "project_name": project_name,
        "folder_name": folder_name,
    }


# =============================================================================
# Author Smart Merge Endpoints
# =============================================================================
#
# Per Phase-2 decision D5 (architectural_change_followup.md §1) the
# smart-merge engine takes a typed v2 :class:`Graph` and derives
# :class:`SourceIdentity` instances from the typed registries
# (``git_accounts`` / ``jira_users`` / ``github_users``). UnifiedUser
# records are persisted into Supabase ``unified_users`` /
# ``user_identity_mappings`` / ``rejected_similarities`` (unchanged).
#
# The Phase-1 legacy ``graph_data`` global is GONE — per-project
# smart-merge state (unified-user list, base-graph cache, last-served
# suggestions) lives in :mod:`src.smart_merge.state_store` and is keyed
# by ``project_id``.

def _get_smart_merge_engine() -> AuthorSmartMergeEngine:
    """Create a smart merge engine instance with Supabase persistence."""
    return AuthorSmartMergeEngine(SupabaseSmartMergeRepository())


def _require_loaded_graph(project_id: str) -> Graph | JSONResponse:
    """Return the loaded :class:`Graph` for ``project_id`` or a 400 JSON.

    Smart-merge endpoints used to gate on
    ``not graph_data or current_project_id != project_id``; the v2
    equivalent is "is a Graph loaded for this project in
    ``graph_store``?". Returning a :class:`JSONResponse` lets callers
    short-circuit with a plain ``isinstance`` check.
    """
    graph = graph_store.get(project_id)
    if graph is None:
        return JSONResponse(
            {"error": "Project is not loaded. Load the project first."},
            status_code=400,
        )
    return graph


def _get_activity_counts(graph: Graph) -> dict[str, int]:
    """Compute activity counts for every identity in the loaded graph.

    Replaces the Phase-1 helper that read off ``graph_data["git"].account_registry``
    and friends. v2 reads through the typed Graph's secondary indexes
    (``commits.by_author`` etc.) — those are O(1) bucket lookups so even
    a few-thousand-account graph is cheap.
    """
    counts: dict[str, int] = {}

    for account in graph.git_accounts.all():
        key = f"git:{account.id}"
        counts[key] = len(graph.commits.by_author[account.ref()])

    for user in graph.github_users.all():
        key = f"github:{user.id}"
        # PullRequestRegistry only declares ``by_author``; merged_by /
        # assignee aggregates would require a scan or new index. The
        # legacy code summed three lists kept on the GitHubUser entity
        # itself — those back-pointers are gone in v2, so we approximate
        # with the indexed ``by_author`` count, which is what the smart-
        # merge UI primarily sorts on (the most-active account wins the
        # name/email selection).
        counts[key] = len(graph.pull_requests.by_author[user.ref()])

    for user in graph.jira_users.all():
        key = f"jira:{user.id}"
        # IssueRegistry declares all three: by_reporter / by_creator /
        # by_assignee. Sum without dedup so we keep parity with the
        # legacy v1 helper (which also summed three flat lists).
        user_ref = user.ref()
        counts[key] = (
            len(graph.issues.by_reporter[user_ref])
            + len(graph.issues.by_creator[user_ref])
            + len(graph.issues.by_assignee[user_ref])
        )

    return counts


def _replay_user_mappings(project_id: str) -> dict:
    """Replay persisted Supabase user mappings onto the loaded Graph.

    Called by :func:`load_project` after the typed Graph slot is set.
    Re-creates the :class:`UnifiedUser` API objects from the persisted
    ``unified_users`` + ``user_identity_mappings`` rows, binds each to
    the typed Graph so the per-instance stats accessors resolve live,
    and stores them in :mod:`smart_merge_state_store`.
    """
    repo = SupabaseSmartMergeRepository()
    mappings = repo.get_user_mappings(project_id)

    state = smart_merge_state_store.reset(project_id)

    graph = graph_store.get(project_id)
    if graph is None:
        # Defensive — the only caller is load_project, which sets the
        # graph before calling here. If this fires, something's racing.
        return {"users_replayed": 0, "identities_matched": 0, "identities_missing": 0}

    if not mappings:
        return {"users_replayed": 0, "identities_matched": 0, "identities_missing": 0}

    users: list[UnifiedUser] = []
    total_matched = 0
    total_missing = 0

    available_keys = {i.key for i in extract_all_identities(graph)}

    for mapping in mappings:
        matched_identities = []
        for identity in mapping.identities:
            if identity.key in available_keys:
                matched_identities.append(identity)
                total_matched += 1
            else:
                total_missing += 1
                logger.warning(
                    f"Identity {identity.key} not found in current graph "
                    f"(unified user {mapping.unified_user_id})"
                )

        user = UnifiedUser(
            id=mapping.unified_user_id,
            display_name=mapping.display_name,
            primary_email=mapping.primary_email,
            identities=matched_identities,
        )
        user.bind_graph(graph)
        users.append(user)

    state.users = users
    logger.info(
        f"Replayed {len(users)} unified users "
        f"({total_matched} matched, {total_missing} missing)"
    )

    return {
        "users_replayed": len(users),
        "identities_matched": total_matched,
        "identities_missing": total_missing,
    }


@app.get("/projects/{project_id}/authors/suggestions")
async def get_author_suggestions(project_id: str):
    """Compute and return author merge suggestions for the loaded project.

    Uses the smart merge engine to detect likely duplicate identities
    across Git, GitHub, and JIRA sources.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    try:
        state = smart_merge_state_store.get(project_id)
        all_identities = extract_all_identities(graph)
        existing_users: list[UnifiedUser] = state.users
        activity_counts = _get_activity_counts(graph)

        # Exclude identities already bound to a unified user. Re-scans should
        # only propose merges among the still-unmerged identities — otherwise
        # apply would violate uq_identity_mapping.
        mapped_keys = {
            identity.key
            for user in existing_users
            for identity in user.identities
        }
        unmapped_identities = [i for i in all_identities if i.key not in mapped_keys]

        # Invalidate the cached base graph if the unmapped set changed.
        cached_base = state.base_graph
        if cached_base is not None and set(cached_base.nodes.keys()) != {
            i.key for i in unmapped_identities
        }:
            cached_base = None

        engine = _get_smart_merge_engine()
        suggestions, base_graph = engine.compute_suggestions(
            identities=unmapped_identities,
            project_id=project_id,
            existing_users=[],
            activity_counts=activity_counts,
            base_graph=cached_base,
        )
        state.base_graph = base_graph
        state.last_suggestions = {s.suggestion_id: s for s in suggestions}

        return {
            "suggestions": [s.to_dict(activity_counts) for s in suggestions],
            "total_identities": len(all_identities),
            "existing_users": len(existing_users),
        }
    except Exception as e:
        logger.error(f"Failed to compute suggestions: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to compute suggestions: {str(e)}"},
            status_code=500,
        )


@app.get("/projects/{project_id}/authors/suggestions/{suggestion_id}/identities")
async def get_suggestion_identities(
    project_id: str,
    suggestion_id: str,
    offset: int = 0,
    limit: int = MAX_IDENTITIES_PER_SUGGESTION,
):
    """Return a page of identities for a previously computed suggestion.

    The suggestions endpoint caches the full list in memory keyed by
    suggestion_id. The UI calls this endpoint to page through clusters
    larger than MAX_IDENTITIES_PER_SUGGESTION.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    state = smart_merge_state_store.get(project_id)
    suggestion = state.last_suggestions.get(suggestion_id)
    if suggestion is None:
        return JSONResponse(
            {"error": "Suggestion not found. Recompute suggestions and try again."},
            status_code=404,
        )

    if offset < 0:
        offset = 0
    if limit <= 0:
        limit = MAX_IDENTITIES_PER_SUGGESTION

    activity_counts = _get_activity_counts(graph)
    ordered = sorted(
        suggestion.identities,
        key=lambda i: activity_counts.get(i.key, 0),
        reverse=True,
    )
    page = ordered[offset: offset + limit]

    return {
        "suggestion_id": suggestion_id,
        "total_identities": len(suggestion.identities),
        "offset": offset,
        "limit": limit,
        "identities": [
            {
                "source": i.source,
                "source_key": i.source_key,
                "name": i.name,
                "email": i.email,
                "login": i.login,
            }
            for i in page
        ],
    }


@app.post("/projects/{project_id}/authors/suggestions/apply")
async def apply_author_suggestion(project_id: str, request: ApplySuggestionRequest):
    """Apply a merge suggestion: create a UnifiedUser from selected identities.

    Optionally rejects unselected identities from the same suggestion.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    try:
        state = smart_merge_state_store.get(project_id)
        repo = SupabaseSmartMergeRepository()
        all_identities = extract_all_identities(graph)
        identity_lookup = {i.key: i for i in all_identities}

        # Resolve selected identities
        selected: list[SourceIdentity] = []
        for key in request.selected_identity_keys:
            identity = identity_lookup.get(key)
            if identity:
                selected.append(identity)
            else:
                logger.warning(f"Selected identity key not found: {key}")

        if len(selected) < 2:
            return JSONResponse(
                {"error": "At least 2 valid identities are required to create a unified user."},
                status_code=400,
            )

        # Create the unified user
        user = UnifiedUser(
            display_name=request.name,
            primary_email=request.email if request.email != "unknown@unknown" else None,
            identities=selected,
        )
        user.bind_graph(graph)

        # Persist the mapping
        mapping = UserMapping(
            unified_user_id=user.id,
            display_name=user.display_name,
            primary_email=user.primary_email,
            identities=selected,
        )
        repo.upsert_user_mapping(mapping, project_id)

        # Update in-memory state
        state.users.append(user)
        state.invalidate_cache()

        # Handle rejected (unselected) identities
        if request.unselected_identity_keys:
            unselected = [
                identity_lookup[k]
                for k in request.unselected_identity_keys
                if k in identity_lookup
            ]
            pairs = []
            for sel in selected:
                for unsel in unselected:
                    pairs.append(RejectedPair(
                        project_id=project_id,
                        first_source=sel.source,
                        first_source_key=sel.source_key,
                        second_source=unsel.source,
                        second_source_key=unsel.source_key,
                    ))
            if pairs:
                repo.add_rejected_similarities(project_id, pairs)

        return user.to_dict()

    except Exception as e:
        logger.error(f"Failed to apply suggestion: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to apply suggestion: {str(e)}"},
            status_code=500,
        )


@app.post("/projects/{project_id}/authors/suggestions/apply-batch")
async def apply_author_suggestions_batch(project_id: str):
    """Bulk-apply every cached suggestion using its default name/email and all identities.

    Expects the caller to have just fetched suggestions (so the cache is populated).
    Partial failures are reported but do not abort the loop.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    state = smart_merge_state_store.get(project_id)
    cache = state.last_suggestions
    if not cache:
        return JSONResponse(
            {"error": "No cached suggestions. Compute suggestions first."},
            status_code=400,
        )

    suggestions = list(cache.values())
    repo = SupabaseSmartMergeRepository()

    created_dtos: list[dict] = []
    failures: list[dict] = []

    for suggestion in suggestions:
        try:
            if len(suggestion.identities) < 2:
                failures.append({
                    "suggestion_id": suggestion.suggestion_id,
                    "error": "Suggestion has fewer than 2 identities.",
                })
                continue

            email = suggestion.default_email
            primary_email = email if email and email != "unknown@unknown" else None

            user = UnifiedUser(
                display_name=suggestion.default_name,
                primary_email=primary_email,
                identities=list(suggestion.identities),
            )
            user.bind_graph(graph)

            mapping = UserMapping(
                unified_user_id=user.id,
                display_name=user.display_name,
                primary_email=user.primary_email,
                identities=user.identities,
            )
            repo.upsert_user_mapping(mapping, project_id)

            state.users.append(user)
            created_dtos.append(user.to_dict())

        except Exception as e:
            logger.error(
                f"Failed to apply suggestion {suggestion.suggestion_id} in batch: {e}",
                exc_info=True,
            )
            failures.append({
                "suggestion_id": suggestion.suggestion_id,
                "error": str(e),
            })

    state.invalidate_cache()

    return {
        "created": len(created_dtos),
        "failed": failures,
        "users": created_dtos,
    }


@app.post("/projects/{project_id}/authors/suggestions/reject")
async def reject_author_suggestion(project_id: str, request: RejectSuggestionRequest):
    """Reject a merge suggestion: persist rejected pairs so they don't reappear."""
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    try:
        state = smart_merge_state_store.get(project_id)
        repo = SupabaseSmartMergeRepository()
        all_identities = extract_all_identities(graph)
        identity_lookup = {i.key: i for i in all_identities}

        identities = [
            identity_lookup[k]
            for k in request.identity_keys
            if k in identity_lookup
        ]

        if len(identities) < 2:
            return JSONResponse(
                {"error": "At least 2 valid identity keys required."},
                status_code=400,
            )

        # Create all pairwise rejected pairs
        pairs = []
        for i, a in enumerate(identities):
            for b in identities[i + 1:]:
                pairs.append(RejectedPair(
                    project_id=project_id,
                    first_source=a.source,
                    first_source_key=a.source_key,
                    second_source=b.source,
                    second_source_key=b.source_key,
                ))

        repo.add_rejected_similarities(project_id, pairs)
        state.invalidate_cache()

        return {"ok": True, "rejected_pairs": len(pairs)}

    except Exception as e:
        logger.error(f"Failed to reject suggestion: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to reject suggestion: {str(e)}"},
            status_code=500,
        )


@app.get("/projects/{project_id}/authors/users")
async def get_unified_users(project_id: str):
    """Get the current list of unified users for a project.

    Returns both persisted and in-memory state.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    state = smart_merge_state_store.get(project_id)
    return {
        "users": [u.to_dict() for u in state.users],
        "total": len(state.users),
    }


@app.delete("/projects/{project_id}/authors/users/{unified_user_id}")
async def delete_unified_user(project_id: str, unified_user_id: str):
    """Delete a unified user and its identity mappings.

    Removes the unified user from both database (cascade-deletes identity
    mappings) and in-memory graph state.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    try:
        state = smart_merge_state_store.get(project_id)
        repo = SupabaseSmartMergeRepository()
        repo.delete_user_mapping(project_id, unified_user_id)

        state.users = [u for u in state.users if u.id != unified_user_id]
        state.invalidate_cache()

        return {"ok": True, "deleted_id": unified_user_id}

    except Exception as e:
        logger.error(f"Failed to delete unified user: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to delete unified user: {str(e)}"},
            status_code=500,
        )


@app.delete("/projects/{project_id}/authors/users")
async def delete_all_unified_users(project_id: str):
    """Reset author matching for a project as if it was freshly created.

    Wipes unified users + identity mappings + rejected-pair history in
    Supabase, clears the in-memory users list, and invalidates the
    smart-merge cache.

    Chunk 19: the Phase-1 ``_persist_project_pickle`` call (which would
    have raised ``ImportError: upload_pickle_to_supabase``) is gone —
    v2 persists each typed registry on /build via :meth:`Graph.dump`,
    and smart-merge state is the Supabase tables themselves. Deleting
    the rows IS the persistence operation.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    try:
        state = smart_merge_state_store.get(project_id)
        repo = SupabaseSmartMergeRepository()
        deleted_users = repo.delete_all_user_mappings(project_id)
        deleted_rejected = repo.delete_all_rejected_similarities(project_id)

        state.users = []
        state.invalidate_cache()

        return {
            "ok": True,
            "deleted_users": deleted_users,
            "deleted_rejected": deleted_rejected,
        }

    except Exception as e:
        logger.error(f"Failed to reset author matching: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to reset author matching: {str(e)}"},
            status_code=500,
        )


@app.post("/projects/{project_id}/save-graph-state")
async def save_graph_state(project_id: str):
    """Snapshot endpoint — preserved for the web-UI smart-merge button.

    Chunk 19: this used to call the dormant ``_persist_project_pickle``
    (which would have raised ``ImportError: upload_pickle_to_supabase``
    at runtime since the v2 processor stopped exporting that symbol —
    flagged in chunk_10_cleanup §H.2). In v2 there is no single
    ``graph.pkl`` blob to upload: the typed Graph is persisted per
    registry under ``/tmp/pickles/<project_id>/`` by :meth:`Graph.dump`
    at build time, and smart-merge state lives in Supabase
    (``unified_users`` / ``user_identity_mappings`` /
    ``rejected_similarities``) so each apply / reject / delete call IS
    the durable write.

    The endpoint stays mounted so the existing UI button doesn't 404; it
    now returns ``ok`` with the live user count for parity with the v1
    response shape. If a future need surfaces to dump the typed Graph
    on demand, call :meth:`Graph.dump` here against a fresh
    :class:`PickleStore`.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    state = smart_merge_state_store.get(project_id)
    return {
        "ok": True,
        "size_mb": 0.0,  # No upload happens; field preserved for v1 wire compat.
        "user_count": len(state.users),
    }


@app.post("/projects/{project_id}/authors/users/replay")
async def replay_unified_users(project_id: str):
    """Replay persisted user mappings onto the loaded graph.

    Called automatically on project load, but can be triggered manually.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    try:
        result = _replay_user_mappings(project_id)
        return result
    except Exception as e:
        logger.error(f"Failed to replay user mappings: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to replay user mappings: {str(e)}"},
            status_code=500,
        )


# =============================================================================
# Enrichment endpoints (legacy Enrichments blob removed in Chunk 10).
# The v2 typed Graph exposes graph.traits / graph.classifiers /
# graph.relations directly; agents should query them via the MCP sandbox
# (see src/sandbox/inject.py). Per-source summary endpoints (lizard,
# code-structure, duplication, quality-issues) were also dropped — the same
# data is available on the typed registries on graph_store.get(project_id).
# =============================================================================


@app.post("/execute")
async def execute_code(request: CodeRequest):
    """Execute arbitrary Python code against the loaded project graph.

    **Available variables:**
    - `graph_data` — :class:`MCPSandboxView` wrapping the loaded typed Graph

    **Example code:**
    ```python
    commits = graph_data.commits.all()
    print(f'Total commits: {len(commits)}')

    for commit in commits[:5]:
        print(f'{commit.id[:8]} - {commit.message[:50]}')
    ```
    """
    code = request.code
    stdout = io.StringIO()

    try:
        sys_stdout = sys.stdout
        sys.stdout = stdout

        # Execute code with limited scope. Chunk 9: ``graph_data`` is
        # now the :class:`MCPSandboxView` wrapping the typed v2 Graph.
        v2_graph: Optional[Graph] = (
            graph_store.get(current_project_id) if current_project_id else None
        )
        sandbox_view: Optional[MCPSandboxView] = (
            MCPSandboxView(v2_graph) if v2_graph is not None else None
        )
        exec_globals = {
            "graph_data": sandbox_view,
            "graph": v2_graph,  # raw Graph still exposed for power users
            # Free helpers for the three legacy entity-side navigations
            # (plan §11 rows 4–6). Same names the legacy sandbox exposed;
            # the implementations now read off the typed Graph.
            "commit_issues": _sandbox_commit_issues,
            "issue_commits": _sandbox_issue_commits,
            "pr_commits": _sandbox_pr_commits,
            # Bind the view's per-method helpers as top-level callables
            # too, so legacy snippets calling ``find_files_with_trait("x")``
            # without going through ``graph_data.`` still work.
            "find_files_with_trait": (
                sandbox_view.find_files_with_trait if sandbox_view else lambda *_a, **_k: []
            ),
            "cochange_neighbors": (
                sandbox_view.cochange_neighbors if sandbox_view else lambda *_a, **_k: []
            ),
            "overview_as_dict": (
                sandbox_view.overview_as_dict if sandbox_view else lambda *_a, **_k: None
            ),
        }
        exec(code, exec_globals)

        output = stdout.getvalue()
        return JSONResponse({"output": output})
    except Exception:
        tb = traceback.format_exc()
        return JSONResponse({"error": tb}, status_code=400)
    finally:
        sys.stdout = sys_stdout


@app.post("/plot")
async def generate_plot(request: CodeRequest):
    """Execute Python code that generates a matplotlib plot and return
    it as a JPEG image.

    **Available variables:**
    - `graph_data` — :class:`MCPSandboxView` wrapping the loaded typed Graph
    - `plt` — matplotlib.pyplot module
    """
    code = request.code
    stdout = io.StringIO()

    try:
        sys_stdout = sys.stdout
        sys.stdout = stdout

        v2_graph: Optional[Graph] = (
            graph_store.get(current_project_id) if current_project_id else None
        )
        sandbox_view: Optional[MCPSandboxView] = (
            MCPSandboxView(v2_graph) if v2_graph is not None else None
        )
        exec_globals = {
            "graph_data": sandbox_view,
            "graph": v2_graph,
            "plt": plt,
            "commit_issues": _sandbox_commit_issues,
            "issue_commits": _sandbox_issue_commits,
            "pr_commits": _sandbox_pr_commits,
            "find_files_with_trait": (
                sandbox_view.find_files_with_trait if sandbox_view else lambda *_a, **_k: []
            ),
            "cochange_neighbors": (
                sandbox_view.cochange_neighbors if sandbox_view else lambda *_a, **_k: []
            ),
            "overview_as_dict": (
                sandbox_view.overview_as_dict if sandbox_view else lambda *_a, **_k: None
            ),
        }

        exec(code, exec_globals)

        fig = plt.gcf()
        img_bytes = io.BytesIO()
        fig.savefig(img_bytes, format="jpg", bbox_inches="tight")
        img_bytes.seek(0)
        plt.close(fig)

        return Response(content=img_bytes.getvalue(), media_type="image/jpeg")

    except Exception:
        tb = traceback.format_exc()
        return JSONResponse({"error": tb}, status_code=400)

    finally:
        sys.stdout = sys_stdout
