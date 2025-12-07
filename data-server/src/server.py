import io
import sys
import traceback
import shutil
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse, Response
from contextlib import asynccontextmanager
import matplotlib.pyplot as plt

from src.inspector_git.reader.iglog.readers.ig_log_reader import IGLogReader
from src.inspector_git.linker.transformers import GitProjectTransformer
from src.jira_miner.reader_dto.loader import JiraJsonLoader
from src.jira_miner.linker.transformers import JiraProjectTransformer
from src.github_miner.reader_dto.loader import GithubJsonLoader
from src.github_miner.linker.transformers import GitHubProjectTransformer
from src.common.project_linkers import ProjectLinker

from src.middleware.auth import verify_jwt_token, UserContext
from src.supabase_client import get_service_client, get_user_client
from src.graph_store import graph_store
from src.services.project_service import (
    fetch_project_files,
    download_project_files_to_temp,
    update_project_status,
    validate_all_files_present,
)
from src.logger import get_logger

LOG = get_logger(__name__)


# =============================================================================
# Request/Response Models
# =============================================================================

class CodeRequest(BaseModel):
    """Request body for code execution endpoints."""
    code: str = Field(
        ...,
        description="Python code to execute against the project graph",
        json_schema_extra={
            "example": "commits = graph_data['git'].git_commit_registry.all\nprint(f'Total commits: {len(commits)}')"
        }
    )


class BuildResponse(BaseModel):
    """Response from project build endpoint."""
    message: str = Field(..., description="Status message")
    project_id: str = Field(..., description="UUID of the project")
    status: str = Field(..., description="New project status (ready/error)")


class UnloadResponse(BaseModel):
    """Response from project unload endpoint."""
    message: str = Field(..., description="Status message")
    project_id: str = Field(..., description="UUID of the project")


class HealthResponse(BaseModel):
    """Response from health check endpoint."""
    status: str = Field(..., description="Server status")
    loaded_projects: List[str] = Field(..., description="List of project IDs currently loaded in memory")


class ExecuteResponse(BaseModel):
    """Response from code execution endpoint."""
    output: Optional[str] = Field(None, description="Captured stdout from code execution")
    error: Optional[str] = Field(None, description="Error traceback if execution failed")


class ErrorResponse(BaseModel):
    """Standard error response."""
    detail: str = Field(..., description="Error message")


def build_project_graph(
    git_file_path: Path, jira_file_path: Path, github_file_path: Path
) -> dict:
    """
    Builds the project graph from file paths and links them together.

    Args:
        git_file_path: Path to .iglog file
        jira_file_path: Path to JIRA JSON file
        github_file_path: Path to GitHub JSON file

    Returns:
        Dict with 'git', 'jira', 'github' keys containing project objects
    """
    # InspectorGit
    with open(git_file_path, "r", encoding="utf-8") as f:
        git_log_dto = IGLogReader().read(f)

    git_project = GitProjectTransformer(
        git_log_dto,
        name=git_file_path.stem,
        compute_annotated_lines=False,  # no blame
    ).transform()

    # Jira
    jira_loader = JiraJsonLoader(str(jira_file_path))
    jira_data = jira_loader.load()
    jira_project = JiraProjectTransformer(jira_data, name="Jira Project").transform()

    # GitHub
    github_loader = GithubJsonLoader(str(github_file_path))
    github_data = github_loader.load()
    github_project = GitHubProjectTransformer(github_data, name="GitHub Project").transform()

    # Link
    ProjectLinker.link_projects(github_project, jira_project, jira_data)
    ProjectLinker.link_projects(jira_project, git_project)
    ProjectLinker.link_projects(github_project, git_project)

    return {
        "git": git_project,
        "jira": jira_project,
        "github": github_project,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - startup and shutdown logic."""
    LOG.info("Starting data-server...")
    try:
        yield
    finally:
        graph_store.clear()
        LOG.info("Shutdown complete - all graphs cleared from memory.")


# =============================================================================
# FastAPI Application
# =============================================================================

TAGS_METADATA = [
    {
        "name": "Health",
        "description": "Server health and status endpoints. No authentication required.",
    },
    {
        "name": "Projects",
        "description": "Project lifecycle management - build, unload graphs from memory.",
    },
    {
        "name": "Execution",
        "description": "Execute Python code against loaded project graphs.",
    },
]

app = FastAPI(
    title="ScriptBeeAssistant Data Server",
    description="""
## Overview

FastAPI backend for ScriptBeeAssistant - loads serialized project data (Git, GitHub, JIRA)
into in-memory graphs and exposes endpoints for querying via Python code execution.

## Authentication

All endpoints except `/health` require a valid JWT token in the Authorization header:

```
Authorization: Bearer <your_jwt_token>
```

JWT tokens are obtained via Supabase authentication.

## Graph Data Structure

Once a project is built, the `graph_data` variable available in execute/plot endpoints contains:

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

## Workflow

1. Upload files via web-ui (git.iglog, github.json, jira.json)
2. Call `POST /projects/{id}/build` to load graph into memory
3. Call `POST /projects/{id}/execute` to run queries
4. Call `DELETE /projects/{id}/unload` when done
    """,
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=TAGS_METADATA,
    docs_url="/docs",
    redoc_url="/redoc",
)


# =============================================================================
# Endpoints
# =============================================================================

@app.get(
    "/health",
    tags=["Health"],
    response_model=HealthResponse,
    summary="Health check",
    description="Check if the server is running and see which projects are loaded in memory.",
)
async def health_check():
    """Health check endpoint - no auth required."""
    return {"status": "ok", "loaded_projects": graph_store.get_all_project_ids()}


@app.post(
    "/projects/{project_id}/build",
    tags=["Projects"],
    response_model=BuildResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Missing required files"},
        401: {"model": ErrorResponse, "description": "Invalid or missing JWT token"},
        500: {"model": ErrorResponse, "description": "Build failed"},
    },
    summary="Build project graph",
    description="""
Download project files from Supabase Storage, parse them, build the linked graph,
and store it in memory. Updates project status to 'ready' on success or 'error' on failure.

**Prerequisites:** All 3 files must be uploaded (git.iglog, github.json, jira.json)
    """,
)
async def build_project(
    project_id: str,
    user: UserContext = Depends(verify_jwt_token),
):
    service_client = get_service_client()
    user_client = get_user_client(user.token)  # For RLS-enforced queries

    git_path = None
    jira_path = None
    github_path = None

    try:
        # Update status to processing
        await update_project_status(service_client, project_id, "processing")

        # Fetch file metadata
        files_dict = await fetch_project_files(user_client, project_id)

        # Validate all files present
        all_present, missing = validate_all_files_present(files_dict)
        if not all_present:
            await update_project_status(service_client, project_id, "error")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing required files: {', '.join(missing)}",
            )

        # Download files to temp directory
        git_path, jira_path, github_path = await download_project_files_to_temp(
            user_client, files_dict
        )

        # Build graph
        LOG.info(f"Building graph for project {project_id}...")
        graph_data = build_project_graph(git_path, jira_path, github_path)

        # Store in memory
        graph_store.set(project_id, graph_data)

        # Update status to ready
        await update_project_status(service_client, project_id, "ready")

        LOG.info(f"Successfully loaded project {project_id}")
        return BuildResponse(
            message="Project loaded successfully",
            project_id=project_id,
            status="ready",
        )

    except HTTPException:
        raise
    except Exception as e:
        LOG.error(f"Failed to build project {project_id}: {str(e)}", exc_info=True)
        await update_project_status(service_client, project_id, "error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to build project: {str(e)}",
        )
    finally:
        # Cleanup temp directory (get parent of any downloaded file)
        if git_path and git_path.parent.exists():
            shutil.rmtree(git_path.parent, ignore_errors=True)
        elif jira_path and jira_path.parent.exists():
            shutil.rmtree(jira_path.parent, ignore_errors=True)
        elif github_path and github_path.parent.exists():
            shutil.rmtree(github_path.parent, ignore_errors=True)


@app.post(
    "/projects/{project_id}/execute",
    tags=["Execution"],
    response_model=ExecuteResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Graph not loaded or execution error"},
        401: {"model": ErrorResponse, "description": "Invalid or missing JWT token"},
    },
    summary="Execute Python code",
    description="""
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

**Note:** The project must be built first via `/projects/{project_id}/build`
    """,
)
async def execute_code(
    project_id: str,
    request: CodeRequest,
    user: UserContext = Depends(verify_jwt_token),
):
    # Get graph from store
    graph_data = graph_store.get(project_id)
    if not graph_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Graph not loaded. Call /projects/{project_id}/build first",
        )

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


@app.post(
    "/projects/{project_id}/plot",
    tags=["Execution"],
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "Generated plot as JPEG"},
        400: {"model": ErrorResponse, "description": "Graph not loaded or execution error"},
        401: {"model": ErrorResponse, "description": "Invalid or missing JWT token"},
    },
    summary="Generate matplotlib plot",
    description="""
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

**Note:** The project must be built first via `/projects/{project_id}/build`
    """,
)
async def generate_plot(
    project_id: str,
    request: CodeRequest,
    user: UserContext = Depends(verify_jwt_token),
):
    # Get graph from store
    graph_data = graph_store.get(project_id)
    if not graph_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Graph not loaded. Call /projects/{project_id}/build first",
        )

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


@app.delete(
    "/projects/{project_id}/unload",
    tags=["Projects"],
    response_model=UnloadResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing JWT token"},
    },
    summary="Unload project from memory",
    description="""
Remove the project graph from memory and update status to 'idle'.

Use this when you're done querying a project to free up server memory.
The project can be rebuilt later via `/projects/{project_id}/build`.
    """,
)
async def unload_project(
    project_id: str,
    user: UserContext = Depends(verify_jwt_token),
):
    """Unload project graph from memory and update status to 'idle'."""
    service_client = get_service_client()

    # Remove from graph store
    was_deleted = graph_store.delete(project_id)

    if not was_deleted:
        LOG.warning(f"Project {project_id} was not loaded")

    # Update status to idle
    await update_project_status(service_client, project_id, "idle")

    LOG.info(f"Unloaded project {project_id}")
    return UnloadResponse(
        message="Project unloaded successfully",
        project_id=project_id,
    )