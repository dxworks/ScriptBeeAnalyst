import io
import sys
import pickle
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
from src.smart_merge.types import RejectedPair, UserMapping

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
    """Health check endpoint."""
    return {
        "status": "ok",
        "mode": "standalone",
        "data_loaded": bool(graph_data),
        "current_project_id": current_project_id,
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
    try:
        response = supabase.table("projects").select("*").eq("id", project_id).single().execute()
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

    # Download and load pickle from Supabase Storage
    try:
        loaded_graph = load_graph_from_supabase(user_id, project_id)
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


@app.delete("/projects/{project_id}/unload")
async def unload_project(project_id: str):
    """
    Unload a project from memory.

    If the specified project is currently loaded, clears it from memory.
    """
    global graph_data, current_project_id, current_project_name, current_user_id

    if current_project_id != project_id:
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
        identities = extract_all_identities(graph_data)
        existing_users: list[UnifiedUser] = graph_data.get("users", [])
        activity_counts = _get_activity_counts()

        engine = _get_smart_merge_engine()
        suggestions = engine.compute_suggestions(
            identities=identities,
            project_id=project_id,
            existing_users=existing_users,
            activity_counts=activity_counts,
        )

        return {
            "suggestions": [s.to_dict() for s in suggestions],
            "total_identities": len(identities),
            "existing_users": len(existing_users),
        }
    except Exception as e:
        logger.error(f"Failed to compute suggestions: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to compute suggestions: {str(e)}"},
            status_code=500,
        )


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

        # Execute code with limited scope
        exec_globals = {"graph_data": graph_data}
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

        # Prepare isolated execution environment
        exec_globals = {
            "graph_data": graph_data,
            "plt": plt,
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
