import io
import sys
import pickle
import traceback
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.responses import JSONResponse, Response
from contextlib import asynccontextmanager
import matplotlib.pyplot as plt

from supabase import create_client, Client
from src.config import SUPABASE_URL, SUPABASE_SERVICE_KEY, GRAPH_USER_ID, GRAPH_PROJECT_ID
from src.logger import get_logger

logger = get_logger("data-server")


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
    logger.info(f"Downloading pickle from Supabase Storage: {storage_path}")

    if not user_id or not project_id:
        raise ValueError("GRAPH_USER_ID and GRAPH_PROJECT_ID must be set in .env file")

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

    size_mb = len(pickle_bytes) / (1024 * 1024)
    logger.info("Pickle loaded successfully from Supabase")
    logger.info(f"Storage path: {storage_path}")
    logger.info(f"Size: {size_mb:.2f} MB")
    logger.info(f"Git commits: {len(graph_data['git'].git_commit_registry.all)}")
    logger.info(f"JIRA issues: {len(graph_data['jira'].issue_registry.all)}")
    logger.info(f"GitHub PRs: {len(graph_data['github'].pull_request_registry.all)}")

    return graph_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - optionally loads graph from Supabase Storage at startup."""
    global graph_data, current_project_id, current_user_id
    logger.info("Starting data-server in STANDALONE mode")

    # Optional: Load graph if GRAPH_PROJECT_ID is set
    if GRAPH_USER_ID and GRAPH_PROJECT_ID:
        logger.info("Loading project data from Supabase Storage")
        try:
            graph_data = load_graph_from_supabase(GRAPH_USER_ID, GRAPH_PROJECT_ID)
            current_project_id = GRAPH_PROJECT_ID
            current_user_id = GRAPH_USER_ID
            logger.info("Server ready - Data loaded and available at http://localhost:8001")
            logger.info("API docs: http://localhost:8001/docs")
        except FileNotFoundError as e:
            logger.warning(f"Warning: {e}")
            logger.warning("Server will start without pre-loaded data")
            logger.warning("You can load projects later via API endpoints")
            logger.info("Server ready at http://localhost:8001")
            logger.info("API docs: http://localhost:8001/docs")
    else:
        logger.info("No GRAPH_PROJECT_ID set - starting without pre-loaded data")
        logger.info("Server ready at http://localhost:8001")
        logger.info("API docs: http://localhost:8001/docs")

    try:
        yield
    finally:
        graph_data.clear()
        current_project_id = None
        current_user_id = None
        logger.info("Shutdown complete - graph cleared from memory")


# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(
    title="ScriptBeeAssistant Data Server (Standalone)",
    description="""
## Overview

FastAPI backend for ScriptBeeAssistant - loads pre-built project graph from Supabase Storage
into memory at startup. Graph is built separately by processor.py.

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

1. Run `processor.py` to build graph and upload pickle to Supabase Storage
2. Server downloads and loads pickle automatically at startup
3. Call `POST /execute` to run Python queries
4. Call `POST /plot` to generate matplotlib visualizations

## Data Source

Pre-built graph pickle in Supabase Storage bucket `project-graphs` at path:
`{user_id}/{project_id}/graph.pkl`
    """,
    version="1.0.0-standalone",
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

        logger.info(f"Project: {project_name}")
        logger.info(f"User ID: {user_id}")
        logger.info(f"Status: {project_status}")

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
        logger.info(f"Unloading previous project: {current_project_id}")
        graph_data.clear()

    # Download and load pickle from Supabase Storage
    try:
        loaded_graph = load_graph_from_supabase(user_id, project_id)
        graph_data.clear()
        graph_data.update(loaded_graph)
        current_project_id = project_id
        current_user_id = user_id

        logger.info("Project loaded successfully")

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
