import asyncio
import io
import sys
import pickle
import tempfile
import threading
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
from src.common.unified_author import SourceIdentity, UnifiedUser
from src.common.author_extractor import extract_all_identities
from src.smart_merge.engine import AuthorSmartMergeEngine
from src.smart_merge.supabase_repository import SupabaseSmartMergeRepository
from src.smart_merge.types import MAX_IDENTITIES_PER_SUGGESTION, RejectedPair, Suggestion, UserMapping
# Chunk 8: legacy enrichment imports are replaced by the v2 pipeline.
# ``compute_enrichments`` / ``Enrichments`` / ``relations/writer`` /
# ``overview/writer`` belong to the pre-refactor enrichment layer; in
# v2 they are subsumed by ``run_pipeline`` over the typed Graph (writes
# directly into ``graph.relations`` / ``graph.traits`` / etc.).
#
# The Supabase enrichment-cache repository is kept around as a legacy
# module so the smart-merge endpoints that still reference it compile;
# Chunk 10 deletes the legacy modules and removes the stub fallbacks
# below. See Chunk 8 handoff "Deferred ports" §enrichment-endpoints for
# the rationale on why every ``/enrichments/...`` endpoint returns a
# 501 in this chunk.
from src.enrichment.config import DEFAULT_CONFIG, EnrichmentConfig
from src.enrichment.repository import SupabaseEnrichmentRepository
from src.enrichment.v2_pipeline import PipelineResult, run_pipeline
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
from dataclasses import replace as dc_replace

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


# Global graph data - loaded at startup
graph_data = {}

# Serializes full-graph snapshot saves against concurrent mutations.
_save_lock = threading.Lock()


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


def load_graph_from_supabase(user_id: str, project_id: str):
    """
    Downloads and loads the project graph from Supabase Storage.

    Args:
        user_id: User UUID for storage path
        project_id: Project UUID for storage path

    Returns:
        Dict with 'git', 'jira', 'github' keys containing project objects
    """
    storage_path = f"{user_id}/{project_id}/graph.pkl"

    if not user_id or not project_id:
        raise ValueError("user_id and project_id are required")

    # Initialize Supabase client with service key (bypasses RLS)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Download pickle from Supabase Storage
    try:
        pickle_bytes = supabase.storage.from_("project-graphs").download(storage_path)
    except Exception as e:
        raise FileNotFoundError(f"Failed to download pickle from Supabase: {storage_path}. Error: {e}")

    # Deserialize pickle
    graph_data = pickle.loads(pickle_bytes)

    # Verify structure
    if not isinstance(graph_data, dict):
        raise ValueError("Pickle file does not contain a dict")

    required_keys = {"git", "jira", "github"}
    missing_keys = required_keys - set(graph_data.keys())
    if missing_keys:
        raise ValueError(f"Pickle missing required keys: {missing_keys}")

    graph_data.setdefault("metrics", {"lizard": []})
    # B2: keep `code_structure` always present (None when no JaFax/CodeFrame
    # ingest happened) so consumers don't need to check `key in dict`.
    graph_data.setdefault("code_structure", None)
    # B3: same convention for DuDe duplication.
    graph_data.setdefault("duplication", None)
    # B4: same convention for Insider quality-issues.
    graph_data.setdefault("quality_issues", None)

    return graph_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - server starts with no data loaded."""
    global graph_data, current_project_id, current_user_id
    logger.info("Data server started on port 8001")

    try:
        yield
    finally:
        if graph_data:
            graph_data.clear()
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

FastAPI backend for ScriptBeeAssistant - dynamically loads project graphs from Supabase Storage
into memory. Graphs are built separately by processor.py background service.

**No authentication required** - this is a standalone development server.

## Graph Data Structure

The `graph_data` variable available in execute/plot endpoints contains:

```python
graph_data = {
    "git": GitProject,      # Commits, files, changes, authors
    "jira": JiraProject,    # Issues, statuses, types, users
    "github": GitHubProject # Pull requests, commits, users
}
```

### Key Registries

- `graph_data['git'].git_commit_registry.all` - List of all Git commits
- `graph_data['git'].account_registry.all` - List of all Git authors
- `graph_data['jira'].issue_registry.all` - List of all JIRA issues
- `graph_data['github'].pull_request_registry.all` - List of all PRs
- `graph_data['users']` - List of UnifiedUser objects (cross-source merged identities, after setup)

## Workflow

1. Upload serialized files via web UI
2. Processor builds graph and uploads pickle to Supabase Storage
3. Call `POST /projects/{id}/load` to load project into memory
4. Call `POST /execute` to run Python queries
5. Call `POST /plot` to generate matplotlib visualizations
6. Call `DELETE /projects/{id}/unload` to free memory

## Data Source

Pre-built graph pickles in Supabase Storage bucket `project-graphs` at path:
`{user_id}/{project_id}/graph.pkl`
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

# Track currently loaded project
current_project_id = None
current_project_name = None
current_user_id = None


@app.get("/health")
async def health_check():
    """Health check endpoint.

    Chunk 8: ``loaded_projects`` reports the v2 ``GraphStore``
    contents. The legacy ``data_loaded`` / ``stats`` block reflects the
    single in-memory ``graph_data`` dict the legacy smart-merge
    endpoints still consult; Chunk 10 collapses both into the typed
    Graph index.
    """
    return {
        "status": "ok",
        "mode": "standalone",
        "data_loaded": bool(graph_data),
        "current_project_id": current_project_id,
        "loaded_projects": graph_store.get_all_project_ids(),
        "stats": {
            "git_commits": len(graph_data.get("git", {}).git_commit_registry.all) if graph_data else 0,
            "jira_issues": len(graph_data.get("jira", {}).issue_registry.all) if graph_data else 0,
            "github_prs": len(graph_data.get("github", {}).pull_request_registry.all) if graph_data else 0,
            "unified_users": len(graph_data.get("users", [])) if graph_data else 0,
        }
    }


@app.get("/projects/current")
async def get_current_project():
    """Get information about the currently loaded project."""
    if not graph_data or not current_project_id:
        return JSONResponse(
            {"message": "No project currently loaded"},
            status_code=404
        )

    return {
        "project_id": current_project_id,
        "project_name": current_project_name,
        "user_id": current_user_id,
        "stats": {
            "git_commits": len(graph_data.get("git", {}).git_commit_registry.all) if graph_data else 0,
            "jira_issues": len(graph_data.get("jira", {}).issue_registry.all) if graph_data else 0,
            "github_prs": len(graph_data.get("github", {}).pull_request_registry.all) if graph_data else 0,
            "unified_users": len(graph_data.get("users", [])) if graph_data else 0,
        }
    }


@app.post("/projects/{project_id}/load")
async def load_project(project_id: str):
    """
    Load a specific project's graph into memory.

    - Queries database to get user_id for the project
    - Downloads pickle from Supabase Storage
    - Unloads any currently loaded project
    - Loads new project into memory

    Returns project stats on success.
    """
    global graph_data, current_project_id, current_project_name, current_user_id

    logger.info(f"Loading project: {project_id}")

    # Initialize Supabase client
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Query database to get project info (especially user_id)
    # Run in a thread so the event loop stays responsive — without this, the
    # /projects/current poll endpoint stalls for the duration of the load,
    # and the web UI cannot pick up the loaded state.
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table("projects").select("*").eq("id", project_id).single().execute()
        )
        project = response.data

        if not project:
            return JSONResponse(
                {"error": f"Project {project_id} not found"},
                status_code=404
            )

        user_id = project["user_id"]
        project_name = project["name"]
        project_status = project["status"]

        # Check if project is ready
        if project_status != "ready":
            return JSONResponse(
                {"error": f"Project is not ready (status: {project_status}). Please process the project first."},
                status_code=400
            )

    except Exception as e:
        logger.error(f"Failed to fetch project from database: {e}")
        return JSONResponse(
            {"error": f"Failed to fetch project: {str(e)}"},
            status_code=500
        )

    # Unload current project if any
    if current_project_id:
        graph_data.clear()

    # Download and load pickle from Supabase Storage.
    # The download + pickle.loads can take 30-60 s on a 344 MB pickle — run it
    # in a worker thread so the asyncio loop stays free for /projects/current
    # polling and other requests.
    try:
        loaded_graph = await asyncio.to_thread(load_graph_from_supabase, user_id, project_id)
        graph_data.clear()
        graph_data.update(loaded_graph)
        current_project_id = project_id
        current_project_name = project_name
        current_user_id = user_id

        logger.info(f"Project {project_id} loaded into memory")

        # Auto-replay persisted user mappings onto the loaded graph
        replay_result = {"users_replayed": 0, "identities_matched": 0, "identities_missing": 0}
        try:
            replay_result = _replay_user_mappings(project_id)
        except Exception as e:
            logger.warning(f"Failed to replay user mappings (non-fatal): {e}")

        # Chunk 8: legacy enrichment-cache path is disabled. The v2 typed
        # Graph runs the pipeline at build time (``processor.build_graph``)
        # and persists the resulting registries via :class:`PickleStore`.
        # TODO chunk 10 cleanup: drop the SupabaseEnrichmentRepository
        # path entirely once the smart-merge endpoints are ported.
        pass

        unified_users_count = len(graph_data.get("users", []))

        return {
            "message": "Project loaded successfully",
            "project_id": project_id,
            "project_name": project_name,
            "user_id": user_id,
            "stats": {
                "git_commits": len(graph_data.get("git", {}).git_commit_registry.all) if graph_data else 0,
                "jira_issues": len(graph_data.get("jira", {}).issue_registry.all) if graph_data else 0,
                "github_prs": len(graph_data.get("github", {}).pull_request_registry.all) if graph_data else 0,
                "unified_users": unified_users_count,
            },
            "replay": replay_result,
        }

    except FileNotFoundError as e:
        logger.error(f"Pickle file not found: {e}")
        return JSONResponse(
            {"error": str(e)},
            status_code=404
        )
    except Exception as e:
        logger.error(f"Failed to load project: {e}")
        return JSONResponse(
            {"error": f"Failed to load project: {str(e)}"},
            status_code=500
        )


@app.post("/projects/{project_id}/build")
async def build_project(project_id: str):
    """Build a typed v2 :class:`Graph` for ``project_id``.

    Chunk 8: drives the new processor end-to-end —
    download → per-source :class:`Transformer` → typed Graph →
    ``run_pipeline`` → persist. The freshly built Graph lands in
    :data:`graph_store` keyed by ``project_id``.

    Today the legacy → bundles bridge (``processor._downloaded_files_to_bundles``)
    raises :class:`NotImplementedError` — the v2 transformers accept
    only entity bundles and the per-source DTO walks ship in Chunk 10.
    Calling /build against a real project surfaces that error verbatim
    so the operator sees what's missing.
    """
    try:
        graph, pipeline_result = await asyncio.to_thread(
            v2_processor.build_graph, project_id
        )
    except NotImplementedError as exc:
        logger.warning(f"build_graph deferred: {exc}")
        return JSONResponse(
            {
                "error": str(exc),
                "deferred": True,
            },
            status_code=501,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_graph failed")
        return JSONResponse(
            {"error": f"build_graph failed: {exc}"},
            status_code=500,
        )

    graph_store.set(project_id, graph)
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
    """
    Unload a project from memory.

    If the specified project is currently loaded, clears it from memory.
    Chunk 8: also drops the typed Graph from :data:`graph_store`.
    """
    global graph_data, current_project_id, current_project_name, current_user_id

    # Drop the typed v2 Graph from the new store (idempotent).
    graph_store.delete(project_id)

    if current_project_id != project_id:
        # Even if the legacy slot wasn't loaded, we've cleared the typed
        # Graph above — surface a 200 here would change the contract
        # vs. legacy callers, so preserve the 400 for the smart-merge
        # path while keeping the graph_store side idempotent.
        return JSONResponse(
            {"error": f"Project {project_id} is not currently loaded"},
            status_code=400
        )

    logger.info(f"Unloading project: {project_id}")

    graph_data.clear()
    current_project_id = None
    current_project_name = None
    current_user_id = None

    logger.info("Project unloaded successfully")

    return {"message": "Project unloaded successfully"}


@app.post("/projects/{project_id}/scaffold-workspace")
async def scaffold_workspace(project_id: str):
    """
    Create the per-project workspace folder for AI agent analysis.

    Creates a directory under WORKSPACE_ROOT with a README containing
    project info, stats, and UUID. Also creates outputs/ and scripts/ subdirs.
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

    # Create directory structure
    workspace_path.mkdir(parents=True, exist_ok=True)
    (workspace_path / "outputs").mkdir(exist_ok=True)
    (workspace_path / "scripts").mkdir(exist_ok=True)

    # Gather stats if project is loaded
    stats_text = ""
    if current_project_id == project_id and graph_data:
        git_commits = len(graph_data.get("git", {}).git_commit_registry.all) if graph_data else 0
        jira_issues = len(graph_data.get("jira", {}).issue_registry.all) if graph_data else 0
        github_prs = len(graph_data.get("github", {}).pull_request_registry.all) if graph_data else 0
        stats_text = (
            f"\n## Statistics\n\n"
            f"- Git commits: {git_commits}\n"
            f"- JIRA issues: {jira_issues}\n"
            f"- GitHub PRs: {github_prs}\n"
        )

    # Generate README
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

def _get_smart_merge_engine() -> AuthorSmartMergeEngine:
    """Create a smart merge engine instance with Supabase persistence."""
    return AuthorSmartMergeEngine(SupabaseSmartMergeRepository())


def _invalidate_smart_merge_cache() -> None:
    """Drop cached base graph and last suggestions after any merge/reject/delete."""
    graph_data.pop("smart_merge_base_graph", None)
    graph_data.pop("smart_merge_last_suggestions", None)


def _get_activity_counts() -> dict[str, int]:
    """Compute activity counts for all identities in the loaded graph."""
    counts: dict[str, int] = {}
    git_project = graph_data.get("git")
    if git_project:
        for account in git_project.account_registry.all:
            key = f"git:{account.id}"
            counts[key] = len(account.commits)

    github_project = graph_data.get("github")
    if github_project:
        for user in github_project.git_hub_user_registry.all:
            key = f"github:{user.url}"
            total = (
                len(user.pull_requests_as_creator)
                + len(user.pull_requests_as_merged_by)
                + len(user.pull_requests_as_assignee)
            )
            counts[key] = total

    jira_project = graph_data.get("jira")
    if jira_project:
        for user in jira_project.jira_user_registry.all:
            key = f"jira:{user.link}"
            total = (
                len(user.issues_as_reporter)
                + len(user.issues_as_creator)
                + len(user.issues_as_assignee)
            )
            counts[key] = total

    return counts


def _replay_user_mappings(project_id: str) -> dict:
    """
    Replay persisted user mappings onto the loaded graph.
    Called automatically after project load.
    """
    repo = SupabaseSmartMergeRepository()
    mappings = repo.get_user_mappings(project_id)

    if not mappings:
        graph_data["users"] = []
        return {"users_replayed": 0, "identities_matched": 0, "identities_missing": 0}

    users: list[UnifiedUser] = []
    total_matched = 0
    total_missing = 0

    # Build a set of available identity keys from the current graph
    all_identities = extract_all_identities(graph_data)
    available_keys = {i.key for i in all_identities}

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
        user.bind_graph(graph_data)
        users.append(user)

    graph_data["users"] = users
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
    """
    Compute and return author merge suggestions for the loaded project.

    Uses the smart merge engine to detect likely duplicate identities
    across Git, GitHub, and JIRA sources.
    """
    if not graph_data or current_project_id != project_id:
        return JSONResponse(
            {"error": "Project is not loaded. Load the project first."},
            status_code=400,
        )

    try:
        all_identities = extract_all_identities(graph_data)
        existing_users: list[UnifiedUser] = graph_data.get("users", [])
        activity_counts = _get_activity_counts()

        # Exclude identities already bound to a unified user. Re-scans should
        # only propose merges among the still-unmerged identities — otherwise
        # apply would violate uq_identity_mapping.
        mapped_keys = {
            identity.key
            for user in existing_users
            for identity in user.identities
        }
        unmapped_identities = [i for i in all_identities if i.key not in mapped_keys]

        # Invalidate the cached base graph if the unmapped set changed (e.g. a
        # user was deleted, freeing identities back into the pool).
        cached_base = graph_data.get("smart_merge_base_graph")
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
        graph_data["smart_merge_base_graph"] = base_graph
        graph_data["smart_merge_last_suggestions"] = {
            s.suggestion_id: s for s in suggestions
        }

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
    if not graph_data or current_project_id != project_id:
        return JSONResponse(
            {"error": "Project is not loaded. Load the project first."},
            status_code=400,
        )

    cache: dict[str, Suggestion] = graph_data.get("smart_merge_last_suggestions", {})
    suggestion = cache.get(suggestion_id)
    if suggestion is None:
        return JSONResponse(
            {"error": "Suggestion not found. Recompute suggestions and try again."},
            status_code=404,
        )

    if offset < 0:
        offset = 0
    if limit <= 0:
        limit = MAX_IDENTITIES_PER_SUGGESTION

    activity_counts = _get_activity_counts()
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
    """
    Apply a merge suggestion: create a UnifiedUser from selected identities.

    Optionally rejects unselected identities from the same suggestion.
    """
    if not graph_data or current_project_id != project_id:
        return JSONResponse(
            {"error": "Project is not loaded. Load the project first."},
            status_code=400,
        )

    try:
        repo = SupabaseSmartMergeRepository()
        all_identities = extract_all_identities(graph_data)
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
        from uuid import uuid4
        user = UnifiedUser(
            display_name=request.name,
            primary_email=request.email if request.email != "unknown@unknown" else None,
            identities=selected,
        )
        user.bind_graph(graph_data)

        # Persist the mapping
        mapping = UserMapping(
            unified_user_id=user.id,
            display_name=user.display_name,
            primary_email=user.primary_email,
            identities=selected,
        )
        repo.upsert_user_mapping(mapping, project_id)

        # Update in-memory users list
        existing_users: list[UnifiedUser] = graph_data.get("users", [])
        existing_users.append(user)
        graph_data["users"] = existing_users
        _invalidate_smart_merge_cache()

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
    """
    Bulk-apply every cached suggestion using its default name/email and all identities.

    Expects the caller to have just fetched suggestions (so the cache is populated).
    Partial failures are reported but do not abort the loop.
    """
    if not graph_data or current_project_id != project_id:
        return JSONResponse(
            {"error": "Project is not loaded. Load the project first."},
            status_code=400,
        )

    cache: dict[str, Suggestion] = graph_data.get("smart_merge_last_suggestions", {})
    if not cache:
        return JSONResponse(
            {"error": "No cached suggestions. Compute suggestions first."},
            status_code=400,
        )

    suggestions = list(cache.values())
    repo = SupabaseSmartMergeRepository()
    existing_users: list[UnifiedUser] = graph_data.get("users", [])

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
            user.bind_graph(graph_data)

            mapping = UserMapping(
                unified_user_id=user.id,
                display_name=user.display_name,
                primary_email=user.primary_email,
                identities=user.identities,
            )
            repo.upsert_user_mapping(mapping, project_id)

            existing_users.append(user)
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

    graph_data["users"] = existing_users
    _invalidate_smart_merge_cache()

    return {
        "created": len(created_dtos),
        "failed": failures,
        "users": created_dtos,
    }


@app.post("/projects/{project_id}/authors/suggestions/reject")
async def reject_author_suggestion(project_id: str, request: RejectSuggestionRequest):
    """
    Reject a merge suggestion: persist rejected pairs so they don't reappear.
    """
    if not graph_data or current_project_id != project_id:
        return JSONResponse(
            {"error": "Project is not loaded. Load the project first."},
            status_code=400,
        )

    try:
        repo = SupabaseSmartMergeRepository()
        all_identities = extract_all_identities(graph_data)
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
        _invalidate_smart_merge_cache()

        return {"ok": True, "rejected_pairs": len(pairs)}

    except Exception as e:
        logger.error(f"Failed to reject suggestion: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to reject suggestion: {str(e)}"},
            status_code=500,
        )


@app.get("/projects/{project_id}/authors/users")
async def get_unified_users(project_id: str):
    """
    Get the current list of unified users for a project.
    Returns both persisted and in-memory state.
    """
    if not graph_data or current_project_id != project_id:
        return JSONResponse(
            {"error": "Project is not loaded. Load the project first."},
            status_code=400,
        )

    users: list[UnifiedUser] = graph_data.get("users", [])
    return {
        "users": [u.to_dict() for u in users],
        "total": len(users),
    }


@app.delete("/projects/{project_id}/authors/users/{unified_user_id}")
async def delete_unified_user(project_id: str, unified_user_id: str):
    """
    Delete a unified user and its identity mappings.

    Removes the unified user from both database (cascade-deletes identity mappings)
    and in-memory graph state.
    """
    if not graph_data or current_project_id != project_id:
        return JSONResponse(
            {"error": "Project is not loaded. Load the project first."},
            status_code=400,
        )

    try:
        repo = SupabaseSmartMergeRepository()
        repo.delete_user_mapping(project_id, unified_user_id)

        # Remove from in-memory users list
        existing_users: list[UnifiedUser] = graph_data.get("users", [])
        graph_data["users"] = [u for u in existing_users if u.id != unified_user_id]
        _invalidate_smart_merge_cache()

        return {"ok": True, "deleted_id": unified_user_id}

    except Exception as e:
        logger.error(f"Failed to delete unified user: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to delete unified user: {str(e)}"},
            status_code=500,
        )


@app.delete("/projects/{project_id}/authors/users")
async def delete_all_unified_users(project_id: str):
    """
    Reset author matching for a project as if it was freshly created.

    Wipes unified users + identity mappings + rejected-pair history in Supabase,
    clears the in-memory users list, invalidates the smart-merge cache, and
    re-saves the on-disk pickle so the snapshot reflects the cleared state.
    Project files in storage are untouched.
    """
    if not graph_data or current_project_id != project_id:
        return JSONResponse(
            {"error": "Project is not loaded. Load the project first."},
            status_code=400,
        )

    if not current_user_id:
        return JSONResponse(
            {"error": "No user context for the loaded project."},
            status_code=400,
        )

    try:
        repo = SupabaseSmartMergeRepository()
        deleted_users = repo.delete_all_user_mappings(project_id)
        deleted_rejected = repo.delete_all_rejected_similarities(project_id)

        graph_data["users"] = []
        _invalidate_smart_merge_cache()

        with _save_lock:
            _persist_project_pickle(project_id)

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


def _persist_project_pickle(project_id: str) -> tuple[int, int]:
    """
    Snapshot the current in-memory graph (including unified users) to a pickle
    and upload it to Supabase Storage.

    Returns (size_bytes, user_count). Raises on failure — callers translate to
    HTTP responses. Caller must hold _save_lock.
    """
    from src.processor import upload_pickle_to_supabase

    users: list[UnifiedUser] = graph_data.get("users", [])
    to_pickle = {
        "git": graph_data.get("git"),
        "jira": graph_data.get("jira"),
        "github": graph_data.get("github"),
        "users": users,
    }

    # Break the user -> graph_data back-reference so we don't serialize the
    # whole live dict (caches, etc.) through the user objects.
    for user in users:
        user.bind_graph(None)

    tmp_path: Optional[Path] = None
    # Large project graphs have deep bidirectional refs (commits <-> parents,
    # issues <-> PRs <-> commits). Python's default recursion limit of ~1000
    # isn't enough for pickle to walk them.
    previous_recursion_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(50000)
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tmp:
            pickle.dump(to_pickle, tmp, protocol=pickle.HIGHEST_PROTOCOL)
            tmp_path = Path(tmp.name)

        upload_pickle_to_supabase(tmp_path, current_user_id, project_id)
        size_bytes = tmp_path.stat().st_size
        return size_bytes, len(users)
    finally:
        sys.setrecursionlimit(previous_recursion_limit)
        # Always restore live bindings, even on failure.
        for user in users:
            user.bind_graph(graph_data)
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to remove temp pickle {tmp_path}: {e}")


@app.post("/projects/{project_id}/save-graph-state")
async def save_graph_state(project_id: str):
    """
    Snapshot the current in-memory graph (including unified users) to a pickle
    and upload it to Supabase Storage, replacing the project's existing pickle.

    The pickle becomes a self-contained state snapshot. Supabase merge tables
    are left intact so subsequent merges/unmerges continue to work normally.
    """
    if not graph_data or current_project_id != project_id:
        return JSONResponse(
            {"error": "Project is not loaded. Load the project first."},
            status_code=400,
        )

    if not current_user_id:
        return JSONResponse(
            {"error": "No user context for the loaded project."},
            status_code=400,
        )

    with _save_lock:
        try:
            size_bytes, user_count = _persist_project_pickle(project_id)
        except Exception as e:
            logger.error(f"Failed to save graph state: {e}", exc_info=True)
            return JSONResponse(
                {"error": f"Failed to save graph state: {str(e)}"},
                status_code=500,
            )

        return {
            "ok": True,
            "size_mb": round(size_bytes / (1024 * 1024), 2),
            "user_count": user_count,
        }


@app.post("/projects/{project_id}/authors/users/replay")
async def replay_unified_users(project_id: str):
    """
    Replay persisted user mappings onto the loaded graph.
    Called automatically on project load, but can be triggered manually.
    """
    if not graph_data or current_project_id != project_id:
        return JSONResponse(
            {"error": "Project is not loaded. Load the project first."},
            status_code=400,
        )

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
# Enrichment Endpoints (tags, relations, overview tables)
# =============================================================================

def _require_enrichments():
    """TODO chunk 10/cleanup: legacy ``Enrichments`` model is gone in v2.

    The v2 typed Graph owns ``graph.relations`` / ``graph.traits`` /
    ``graph.classifiers`` directly; the per-source overview tables are
    still :class:`NotImplementedError` stubs (Chunk 7 handoff §F /
    Deferred ports). Endpoints that used to consult the cached
    ``Enrichments`` payload now consistently return ``None`` here so
    they emit a 501 / empty-CSV instead of crashing.
    """
    return None


@app.get("/enrichments/catalog")
async def get_enrichment_catalog():
    """Live catalog of every classifier slot, anomaly trait, relation kind, and
    overview table the enrichment layer can compute.

    Built by reflecting on `src/enrichment/{tagger,relations,overview}` — does
    NOT require a project to be loaded. Use this to discover what exists; use
    each entry's `source_file` to learn what a metric *means* and its
    `config_fields` (traits only) to find threshold values in EnrichmentConfig.
    """
    from src.enrichment.registry import build_metric_catalog
    return build_metric_catalog()


@app.get("/enrichments/tags")
async def get_enrichment_tags(
    entity_kind: Optional[str] = None,
    classifier: Optional[str] = None,
    value: Optional[str] = None,
    trait: Optional[str] = None,
):
    """Return enrichment tags, optionally filtered.

    Query params:
    - `entity_kind` (file/commit/author/issue/pr/component)
    - `classifier=status&value=active` — mandatory classifier match
    - `trait=anomaly.knowledge.Orphan` — optional concern match
    """
    enrichments = _require_enrichments()
    if enrichments is None:
        return JSONResponse({"error": "No enrichments loaded. Load a project first."}, status_code=400)

    results = list(enrichments.tags_by_entity.values())

    if entity_kind:
        results = [t for t in results if t.entity_kind == entity_kind]
    if classifier:
        results = [t for t in results if classifier in t.classifiers and (value is None or t.classifiers[classifier] == value)]
    if trait:
        results = [t for t in results if any(tr.name == trait for tr in t.traits)]

    return {
        "generated_at": enrichments.generated_at.isoformat(),
        "recent_window_days": enrichments.recent_window_days,
        "count": len(results),
        "tags": [t.model_dump() for t in results],
    }


@app.get("/enrichments/relations/{kind}.csv")
async def get_enrichment_relations_csv(kind: str, window: str = "lifetime"):
    """Stream a relation file as CSV. Shape: source,target,strength.

    `window` is `lifetime` (default) or `recent`.
    """
    enrichments = _require_enrichments()
    if enrichments is None:
        return JSONResponse({"error": "No enrichments loaded. Load a project first."}, status_code=400)

    if window not in ("lifetime", "recent"):
        return JSONResponse({"error": f"window must be 'lifetime' or 'recent', got {window!r}"}, status_code=400)

    # TODO chunk 10/cleanup: stream relations from ``graph.relations``
    # directly. Today the v2 pipeline writes into
    # :class:`RelationRegistry` but the CSV writer for the new shape is
    # deferred — we emit an empty CSV header so the endpoint keeps the
    # legacy contract (downloadable file) without crashing.
    empty_csv = b"source,target,strength\n"
    return Response(
        content=empty_csv,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{kind}.{window}.csv"'},
    )


@app.get("/enrichments/overviews/{name}.csv")
async def get_enrichment_overview_csv(name: str):
    """Stream an overview table as CSV (lifetime/recent/trend% triples per column)."""
    enrichments = _require_enrichments()
    if enrichments is None:
        return JSONResponse({"error": "No enrichments loaded. Load a project first."}, status_code=400)

    # TODO chunk 10/cleanup: every legacy overview table is a v2
    # :class:`NotImplementedError` stub (Chunk 7 §F). Emit an empty CSV
    # so the endpoint keeps the legacy contract until Chunk 10 ports
    # the OverviewTableBuilder runners.
    empty_csv = b""
    return Response(
        content=empty_csv,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


@app.get("/enrichments/metrics/files")
async def get_file_metrics(min_loc: int = 0, limit: int = 100):
    """Return per-file Lizard metrics (LOC, max CCN, function count).

    Sorted by `sum_nloc` descending. `min_loc` filters out small files;
    `limit` caps the response. Returns an empty list when no Lizard CSV
    was ingested for the loaded project.
    """
    if not graph_data:
        return JSONResponse({"error": "No project loaded."}, status_code=400)

    metrics_block = graph_data.get("metrics") or {}
    metrics = metrics_block.get("lizard") or []
    filtered = [m for m in metrics if m.sum_nloc >= min_loc]
    filtered.sort(key=lambda m: -m.sum_nloc)
    return {
        "count": len(filtered),
        "files": [
            {
                "file_path": m.file_path,
                "source": m.source,
                "sum_nloc": m.sum_nloc,
                "max_ccn": m.max_ccn,
                "avg_ccn": m.avg_ccn,
                "function_count": m.function_count,
                "longest_function_nloc": m.longest_function_nloc,
            }
            for m in filtered[:limit]
        ],
    }


@app.get("/enrichments/code-structure/summary")
async def get_code_structure_summary():
    """JSON summary of the JaFax (B2) code-structure registries.

    Returns counts of types/methods/fields/references and the union set of
    files covered. Returns an empty payload (no `code_structure` key) when
    no JaFax/CodeFrame ingest happened.
    """
    if not graph_data:
        return JSONResponse({"error": "No project loaded."}, status_code=400)

    cs = graph_data.get("code_structure")
    if cs is None:
        return {"loaded": False, "source": None}

    return {
        "loaded": True,
        "source": cs.source,
        "counts": {
            "types": len(cs.type_registry.all),
            "methods": len(cs.method_registry.all),
            "fields": len(cs.field_registry.all),
            "references": len(cs.reference_registry.all),
            "covered_files": len(cs.file_paths),
        },
    }


@app.get("/enrichments/duplication/summary")
async def get_duplication_summary():
    """JSON summary of DuDe (B3) duplication ingest.

    Returns counts of external pairs / internal files plus aggregate totals.
    Returns `{"loaded": False, "source": None}` when no DuDe ingest happened.
    Use this to gate `get_relation_edges("duplication.file-file.*")` calls.
    """
    if not graph_data:
        return JSONResponse({"error": "No project loaded."}, status_code=400)

    dup = graph_data.get("duplication")
    if dup is None:
        return {"loaded": False, "source": None}

    total_external_lines = sum(p.total_block_length for p in dup.external_pairs)
    total_blocks = sum(p.block_count for p in dup.external_pairs)
    total_internal_lines = sum(dup.internal_by_file.values())
    return {
        "loaded": True,
        "source": dup.source,
        "counts": {
            "external_pairs": len(dup.external_pairs),
            "external_blocks": total_blocks,
            "external_total_duplicated_lines": total_external_lines,
            "internal_files": len(dup.internal_by_file),
            "internal_total_duplicated_lines": total_internal_lines,
        },
    }


@app.get("/enrichments/quality-issues/summary")
async def get_quality_issues_summary():
    """JSON summary of the Insider (B4) quality-issues ingest.

    Returns counts of issues / distinct rules / categories / files plus the
    top-10 rules by aggregate occurrence count. Returns
    `{"loaded": False, "source": None}` when no Insider ingest happened.
    Use this to gate `list_anomalies(trait_name="anomaly.codesmell.*")` calls.
    """
    if not graph_data:
        return JSONResponse({"error": "No project loaded."}, status_code=400)

    qi = graph_data.get("quality_issues")
    if qi is None:
        return {"loaded": False, "source": None}

    rule_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for issue in qi.issues:
        rule_counts[issue.rule_name] = rule_counts.get(issue.rule_name, 0) + issue.occurrence_count
        category_counts[issue.category] = category_counts.get(issue.category, 0) + issue.occurrence_count
    top_rules = sorted(rule_counts.items(), key=lambda kv: -kv[1])[:10]
    return {
        "loaded": True,
        "source": qi.source,
        "counts": {
            "records": len(qi.issues),
            "distinct_rules": len(rule_counts),
            "distinct_categories": len(category_counts),
            "covered_files": len(qi.file_paths),
            "total_occurrence_count": sum(rule_counts.values()),
        },
        "top_rules": [
            {"rule_name": name, "occurrence_count": count}
            for name, count in top_rules
        ],
        "category_breakdown": [
            {"category": cat, "occurrence_count": count}
            for cat, count in sorted(category_counts.items(), key=lambda kv: -kv[1])
        ],
    }


@app.get("/enrichments/summary")
async def get_enrichment_summary():
    """JSON summary of what's in the enrichment layer (counts only, no payload)."""
    enrichments = _require_enrichments()
    if enrichments is None:
        return JSONResponse({"error": "No enrichments loaded. Load a project first."}, status_code=400)

    return {
        "generated_at": enrichments.generated_at.isoformat(),
        "recent_window_days": enrichments.recent_window_days,
        "entity_tags_count": len(enrichments.tags_by_entity),
        "relation_files": [
            {"kind": r.kind, "window": r.window, "edges": len(r.relations)}
            for r in enrichments.relations
        ],
        "overviews": [
            {"name": o.name, "entity_kind": o.entity_kind, "rows": len(o.rows), "columns": o.columns}
            for o in enrichments.overviews
        ],
    }


class ReenrichRequest(BaseModel):
    """Optional EnrichmentConfig field overrides for a re-run."""
    overrides: Optional[dict] = None


@app.post("/projects/{project_id}/reenrich")
async def reenrich_project(project_id: str, body: Optional[ReenrichRequest] = None):
    """Recompute enrichments with optional threshold overrides; persist + return summary.

    Body: {"overrides": {"bugmagnet_ratio_min": 0.5, ...}} — keys must match
    EnrichmentConfig fields. Unknown keys are rejected so typos surface early.
    """
    if not graph_data or current_project_id != project_id:
        return JSONResponse(
            {"error": f"Project {project_id} is not currently loaded"},
            status_code=400,
        )

    # TODO chunk 10/cleanup: /reenrich rebuilt the legacy ``Enrichments``
    # payload by walking every tagger / relation builder / overview
    # table. In v2 the equivalent is a fresh ``run_pipeline(graph,
    # cfg)`` on the already-loaded typed Graph — but the pipeline
    # mutates the graph in place (per its design), so the right
    # contract is: clear ``graph.relations`` / ``graph.traits`` /
    # ``graph.classifiers``, re-run. Deferred until Chunk 10's
    # config-overrides port; today we return 501 so callers don't
    # silently get stale state.
    return JSONResponse(
        {"error": "/reenrich is deferred to Chunk 10. The v2 pipeline "
                  "runs at build time; rebuild the project to refresh "
                  "enrichments."},
        status_code=501,
    )


# Only scalar threshold fields are overridable via `/reenrich`.
# Pattern fields (nature_patterns, *_patterns) and structured fields
# (daytime_buckets, issue_age_buckets, resolved_status_categories) are excluded
# to prevent ReDoS and to keep the JSON wire format simple.
_REENRICH_ALLOWED_OVERRIDES: frozenset[str] = frozenset({
    "recent_window_days",
    "idle_threshold_days",
    "churn_focused_max",
    "churn_medium_max",
    "spread_narrow_max",
    "cochange_max_files_per_commit",
    "newcomer_max_days",
    "established_max_days",
    "senior_max_days",
    "bugmagnet_min_bugfix_commits",
    "bugmagnet_ratio_min",
    "orphan_min_commits",
    "hermit_dominance_ratio",
    "busfactor1_min_distinct_authors",
    "shared_knowledge_entropy_min",
    "shared_knowledge_min_distinct_authors",
    "bazaar_distinct_authors_min",
    "cathedral_dominance_ratio",
    "cathedral_min_recent_commits",
    "pulsar_cv_min",
    "pulsar_min_commits",
    "pulsar_min_intervals",
    "pivotfile_cochange_degree_min",
    "tasksbottleneck_open_age_days",
    "tasksbottleneck_min_in_flight",
    "pr_size_xs_max",
    "pr_size_s_max",
    "pr_size_m_max",
    "pr_size_l_max",
    "supernova_net_churn_min",
    "test_orphan_max_cochange_test_count",
    "test_orphan_min_commits",
    "dynamicblob_loc_min",
    "dynamicblob_changes_min",
    # B2 — JaFax / CodeFrame thresholds.
    "zonecrossroad_min_zone_commits",
    "concurrent_zonecrossroad_strict_threshold",
    "feature_encapsulation_wide_files_min",
    "feature_encapsulation_deep_churn_min",
    "feature_encapsulation_high_impact_files_min",
    "feature_encapsulation_scattered_components_min",
})


def _merged_enrichment_config(overrides: dict) -> EnrichmentConfig:
    """Apply override dict onto a fresh EnrichmentConfig copy, validating keys.

    Only scalar threshold fields listed in ``_REENRICH_ALLOWED_OVERRIDES`` may
    be overridden. Unknown keys, regex/pattern fields, and structured fields
    (buckets, tuples) are rejected with ValueError so the caller sees a 400.
    """
    base = EnrichmentConfig()
    if not overrides:
        return base
    valid_fields = {f for f in base.__dataclass_fields__}
    unknown = [k for k in overrides if k not in valid_fields]
    if unknown:
        raise ValueError(f"Unknown EnrichmentConfig fields: {unknown}")
    forbidden = [k for k in overrides if k not in _REENRICH_ALLOWED_OVERRIDES]
    if forbidden:
        raise ValueError(
            f"EnrichmentConfig fields not overridable via /reenrich: {forbidden}"
        )
    return dc_replace(base, **overrides)


# ── /execute helpers ─────────────────────────────────────────────────────────

def _helper_find_files_with_trait(trait_name: str):
    """List file_ids carrying `trait_name` in the loaded enrichments."""
    enrichments = graph_data.get("enrichments")
    if enrichments is None:
        return []
    return [
        t.entity_id for t in enrichments.entities_with_trait(trait_name)
        if t.entity_kind == "file"
    ]


def _helper_cochange_neighbors(file_id: str, window: str = "lifetime", limit: int = 10):
    """Return up to `limit` strongest co-change neighbours of `file_id`."""
    enrichments = graph_data.get("enrichments")
    if enrichments is None:
        return []
    rel_file = enrichments.relation_file("cochange.file-file", window)
    if rel_file is None:
        return []
    out = []
    for r in rel_file.relations:
        other = None
        if r.source_id == file_id:
            other = r.target_id
        elif r.target_id == file_id:
            other = r.source_id
        if other is not None:
            out.append((other, r.strength))
    out.sort(key=lambda x: -x[1])
    return out[:limit]


def _helper_overview_as_dict(name: str):
    """Return an OverviewTable as plain dicts for easy Python consumption."""
    enrichments = graph_data.get("enrichments")
    if enrichments is None:
        return None
    table = enrichments.overview(name)
    if table is None:
        return None
    return {
        "name": table.name,
        "columns": table.columns,
        "rows": {
            row.entity_id: {
                col: cell.model_dump() for col, cell in row.cells.items()
            }
            for row in table.rows
        },
    }


@app.post("/execute")
async def execute_code(request: CodeRequest):
    """
    Execute arbitrary Python code against the loaded project graph.

    **Available variables:**
    - `graph_data` - Dict with 'git', 'jira', 'github' project objects

    **Example code:**
    ```python
    commits = graph_data['git'].git_commit_registry.all
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
        # now the :class:`MCPSandboxView` wrapping the typed v2 Graph —
        # this is the post-refactor agent-facing surface (see
        # ``architectural_changes.md`` §11 and the chunk 9 handoff for
        # the mapping spec). When no project is loaded, ``graph_data``
        # is ``None``; the agent docs already cover that case via
        # ``get_project_status``.
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
    """
    Execute Python code that generates a matplotlib plot and return it as a JPEG image.

    **Available variables:**
    - `graph_data` - Dict with 'git', 'jira', 'github' project objects
    - `plt` - matplotlib.pyplot module

    **Example code:**
    ```python
    commits = graph_data['git'].git_commit_registry.all
    authors = {}
    for c in commits:
        name = c.author.name if c.author else 'Unknown'
        authors[name] = authors.get(name, 0) + 1

    top_authors = sorted(authors.items(), key=lambda x: -x[1])[:10]
    names, counts = zip(*top_authors)
    plt.barh(names, counts)
    plt.xlabel('Commits')
    plt.title('Top 10 Contributors')
    ```
    """
    code = request.code
    stdout = io.StringIO()

    try:
        # Redirect stdout to capture print output
        sys_stdout = sys.stdout
        sys.stdout = stdout

        # Prepare isolated execution environment. Chunk 9: ``graph_data``
        # is the :class:`MCPSandboxView` over the v2 Graph (same shape
        # as /execute — see plan §11 mapping table). Plot helpers
        # (find_files_with_trait / cochange_neighbors / overview_as_dict)
        # plus the three entity-side navigators (commit_issues etc.) are
        # bound here so matplotlib snippets can use them too.
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

        # Run user code
        exec(code, exec_globals)

        # If the user didn't explicitly save/show, try to get current figure
        fig = plt.gcf()

        # Save figure to memory as JPEG
        img_bytes = io.BytesIO()
        fig.savefig(img_bytes, format="jpg", bbox_inches="tight")
        img_bytes.seek(0)
        plt.close(fig)

        # Return as image
        return Response(content=img_bytes.getvalue(), media_type="image/jpeg")

    except Exception:
        tb = traceback.format_exc()
        return JSONResponse({"error": tb}, status_code=400)

    finally:
        sys.stdout = sys_stdout
