import io
import sys
import traceback
from pathlib import Path
from fastapi import FastAPI
from pydantic import BaseModel
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


class CodeRequest(BaseModel):
    """Request body for code execution endpoints."""
    code: str


# Global graph data - loaded at startup
graph_data = {}


def build_projects():
    """
    Builds the project graph from local test-input files and links them together.

    Returns:
        Dict with 'git', 'jira', 'github' keys containing project objects
    """
    base_path = Path(__file__).parent.parent / "test-input"

    print(f"📂 Loading files from: {base_path}")

    # InspectorGit
    iglog_file = base_path / "inspector-git" / "zeppelin.iglog"
    print(f"  - Loading Git data from {iglog_file.name}...")
    with open(iglog_file, "r", encoding="utf-8") as f:
        git_log_dto = IGLogReader().read(f)

    git_project = GitProjectTransformer(
        git_log_dto,
        name=iglog_file.stem,
        compute_annotated_lines=False,  # no blame
    ).transform()

    # Jira
    jira_file = base_path / "jira-miner" / "ZEPPELIN-detailed-issues.json"
    print(f"  - Loading JIRA data from {jira_file.name}...")
    jira_loader = JiraJsonLoader(str(jira_file))
    jira_data = jira_loader.load()
    jira_project = JiraProjectTransformer(jira_data, name="Jira Project").transform()

    # GitHub
    github_file = base_path / "github-miner" / "githubProject.json"
    print(f"  - Loading GitHub data from {github_file.name}...")
    github_loader = GithubJsonLoader(str(github_file))
    github_data = github_loader.load()
    github_project = GitHubProjectTransformer(github_data, name="GitHub Project").transform()

    # Link projects together
    print("🔗 Linking projects...")
    ProjectLinker.link_projects(github_project, jira_project, jira_data)
    ProjectLinker.link_projects(jira_project, git_project)
    ProjectLinker.link_projects(github_project, git_project)

    print("✅ Graph built successfully!")
    print(f"   - Git commits: {len(git_project.git_commit_registry.all)}")
    print(f"   - JIRA issues: {len(jira_project.issue_registry.all)}")
    print(f"   - GitHub PRs: {len(github_project.pull_request_registry.all)}")

    return {
        "git": git_project,
        "jira": jira_project,
        "github": github_project,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - loads graph at startup."""
    global graph_data
    print("\n" + "="*70)
    print("🚀 Starting data-server in STANDALONE mode...")
    print("="*70)
    print("📦 Loading project data from local files...")
    print()
    graph_data = build_projects()
    print()
    print("="*70)
    print("✅ Server ready! Data loaded and available at http://localhost:8001")
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

FastAPI backend for ScriptBeeAssistant - loads serialized project data (Git, GitHub, JIRA)
from local test-input directory into an in-memory graph at startup.

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

1. Server loads data automatically at startup from `test-input/` directory
2. Call `POST /execute` to run Python queries
3. Call `POST /plot` to generate matplotlib visualizations

## Data Source

Files loaded from `data-server/test-input/`:
- `inspector-git/zeppelin.iglog` (12MB)
- `jira-miner/ZEPPELIN-detailed-issues.json` (67MB)
- `github-miner/githubProject.json` (41MB)
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
