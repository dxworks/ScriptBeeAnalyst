import io
import sys
import pickle
import traceback
import logging
from pathlib import Path
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
        "user_id": current_user_id,
        "stats": {
            "git_commits": len(graph_data.get("git", {}).git_commit_registry.all) if graph_data else 0,
            "jira_issues": len(graph_data.get("jira", {}).issue_registry.all) if graph_data else 0,
            "github_prs": len(graph_data.get("github", {}).pull_request_registry.all) if graph_data else 0,
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
    global graph_data, current_project_id, current_user_id

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
        current_user_id = user_id

        logger.info(f"Project {project_id} loaded into memory")

        return {
            "message": "Project loaded successfully",
            "project_id": project_id,
            "project_name": project_name,
            "user_id": user_id,
            "stats": {
                "git_commits": len(graph_data.get("git", {}).git_commit_registry.all) if graph_data else 0,
                "jira_issues": len(graph_data.get("jira", {}).issue_registry.all) if graph_data else 0,
                "github_prs": len(graph_data.get("github", {}).pull_request_registry.all) if graph_data else 0,
            }
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
    global graph_data, current_project_id, current_user_id

    if current_project_id != project_id:
        return JSONResponse(
            {"error": f"Project {project_id} is not currently loaded"},
            status_code=400
        )

    logger.info(f"Unloading project: {project_id}")

    graph_data.clear()
    current_project_id = None
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
