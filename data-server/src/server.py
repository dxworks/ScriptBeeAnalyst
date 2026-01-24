import io
import sys
import pickle
import traceback
from pathlib import Path
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import JSONResponse, Response
from contextlib import asynccontextmanager
import matplotlib.pyplot as plt

from supabase import create_client, Client
from src.config import SUPABASE_URL, SUPABASE_SERVICE_KEY, GRAPH_USER_ID, GRAPH_PROJECT_ID


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
    print(f"📂 Downloading pickle from Supabase Storage: {storage_path}")

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
    print("✅ Pickle loaded successfully from Supabase!")
    print(f"   - Storage path: {storage_path}")
    print(f"   - Size: {size_mb:.2f} MB")
    print(f"   - Git commits: {len(graph_data['git'].git_commit_registry.all)}")
    print(f"   - JIRA issues: {len(graph_data['jira'].issue_registry.all)}")
    print(f"   - GitHub PRs: {len(graph_data['github'].pull_request_registry.all)}")

    return graph_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - optionally loads graph from Supabase Storage at startup."""
    global graph_data
    print("\n" + "="*70)
    print("🚀 Starting data-server in STANDALONE mode...")
    print("="*70)

    # Optional: Load graph if GRAPH_PROJECT_ID is set
    if GRAPH_USER_ID and GRAPH_PROJECT_ID:
        print("📦 Loading project data from Supabase Storage...")
        print()
        try:
            graph_data = load_graph_from_supabase(GRAPH_USER_ID, GRAPH_PROJECT_ID)
            print()
            print("="*70)
            print("✅ Server ready! Data loaded and available at http://localhost:8001")
            print("📖 API docs: http://localhost:8001/docs")
            print("="*70 + "\n")
        except FileNotFoundError as e:
            print(f"⚠️  Warning: {e}")
            print("   Server will start without pre-loaded data.")
            print("   You can load projects later via API endpoints.")
            print()
            print("="*70)
            print("✅ Server ready at http://localhost:8001")
            print("📖 API docs: http://localhost:8001/docs")
            print("="*70 + "\n")
    else:
        print("ℹ️  No GRAPH_PROJECT_ID set - starting without pre-loaded data")
        print()
        print("="*70)
        print("✅ Server ready at http://localhost:8001")
        print("📖 API docs: http://localhost:8001/docs")
        print("="*70 + "\n")

    try:
        yield
    finally:
        graph_data.clear()
        print("🛑 Shutdown complete - graph cleared from memory.")


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


# =============================================================================
# Endpoints
# =============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "mode": "standalone",
        "data_loaded": bool(graph_data),
        "stats": {
            "git_commits": len(graph_data.get("git", {}).git_commit_registry.all) if graph_data else 0,
            "jira_issues": len(graph_data.get("jira", {}).issue_registry.all) if graph_data else 0,
            "github_prs": len(graph_data.get("github", {}).pull_request_registry.all) if graph_data else 0,
        }
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
