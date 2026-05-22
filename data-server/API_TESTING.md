# Data Server API Testing Guide

This guide demonstrates how to test the data-server endpoints. Routes and
example snippets target the v2 typed-Graph surface — `/execute` and `/plot`
are root paths and operate on the **currently-loaded** project (set via
`/projects/{id}/load`), not per-request.

## Prerequisites

1. Supabase running locally (Docker)
2. Data-server running: `uvicorn src.server:app --host 0.0.0.0 --port 8001 --reload`
   (or `docker compose --env-file ../.env up -d` from `data-server/`)
3. Valid JWT token from authentication

## Getting a JWT Token

First, authenticate via the web-ui or Supabase Auth API to get a JWT token:

```bash
# Example: Login via Supabase Auth API
curl -X POST 'http://localhost:8000/auth/v1/token?grant_type=password' \
  -H 'apikey: YOUR_ANON_KEY' \
  -H 'Content-Type: application/json' \
  -d '{
    "email": "user@example.com",
    "password": "password123"
  }'
```

Extract the `access_token` from the response — this is your JWT.

## Session model

The data-server keeps **one project loaded at a time** in memory (see
`/projects/current`). Before calling `/execute` or `/plot`, ensure a project
is loaded by calling either `/projects/{id}/build` (one-shot: builds and
loads) or `/projects/{id}/load` (loads a previously-built graph from the
local pickle store).

## API Endpoints

### 1. Health Check (No Auth)

```bash
curl -X GET http://localhost:8001/health
```

**Response:**
```json
{
  "status": "ok",
  "mode": "standalone",
  "data_loaded": true,
  "current_project_id": "abc-123-def",
  "loaded_projects": ["abc-123-def"],
  "stats": {
    "git_commits": 5622,
    "jira_issues": 6327,
    "github_prs": 5221,
    "unified_users": 0
  }
}
```

### 2. Current Project

```bash
curl -X GET http://localhost:8001/projects/current
```

Returns `{"loaded": false}` when nothing is loaded; otherwise the loaded
project's id/name/stats.

### 3. Build Project Graph

Download a project's files from Supabase Storage, build the typed v2 Graph,
run the enrichment pipeline, and dump per-registry pickles to disk. After
this completes the project is **also loaded** into memory and becomes the
current project.

```bash
curl -X POST http://localhost:8001/projects/PROJECT_UUID/build \
  -H 'Authorization: Bearer YOUR_JWT_TOKEN' \
  -H 'Content-Type: application/json'
```

**Response:**
```json
{
  "schema_version": 2,
  "built_at": "...",
  "pipeline": { "...": "..." }
}
```

**Status Flow:**
- `draft` → `processing` (during build) → `ready` (success) or `error` (failure)

### 4. Load Previously-Built Project

Load a project's persisted graph from `/tmp/pickles/<project_id>/` into
memory and make it the current project.

```bash
curl -X POST http://localhost:8001/projects/PROJECT_UUID/load \
  -H 'Authorization: Bearer YOUR_JWT_TOKEN'
```

### 5. Execute Code

Run Python code against the currently-loaded graph. **No `project_id` in the
path** — the server picks up whatever project is current.

```bash
curl -X POST http://localhost:8001/execute \
  -H 'Authorization: Bearer YOUR_JWT_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "code": "print(f\"Total commits: {len(graph_data.commits.all())}\")"
  }'
```

**Response:**
```json
{
  "output": "Total commits: 5622\n"
}
```

**Available variables (per `src/server.py` `_make_exec_globals`):**
- `graph_data` — `MCPSandboxView` over the typed Graph (the main surface)
- `graph` — raw typed `Graph` for power users
- `commit_issues(commit)`, `issue_commits(issue)`, `pr_commits(pr)` — free
  cross-source navigation helpers (see `src/sandbox/helpers.py`)
- `find_files_with_trait(name)`, `cochange_neighbors(file_id, ...)`,
  `overview_as_dict(name)` — also methods on `graph_data`; exposed as
  top-level callables for ergonomic snippets

**The MCPSandboxView surface** (see `src/sandbox/inject.py`):
- `graph_data.commits` / `.files` / `.issues` / `.pull_requests` — explicit
  typed registries with `.all()` / `.get(id)` / iteration / reverse indexes
- Everything else on the typed Graph falls through via `__getattr__` —
  e.g. `graph_data.relations`, `graph_data.traits`, `graph_data.classifiers`,
  `graph_data.duplications`, `graph_data.quality_issues`,
  `graph_data.code_types`, `graph_data.file_metrics`, etc.
- Helpers: `tags_for(ref)`, `find_files_with_trait(name)`,
  `find_files_with_classifier(dim, value)`, `cochange_neighbors(...)`,
  `overview_as_dict(name)`, `list_metrics()`, `list_overviews()`,
  `list_file_metrics(...)`, `code_structure_summary()`,
  `duplication_summary()`

**Example Code Snippets:**

```python
# Count commits
print(len(graph_data.commits.all()))

# List JIRA issues
for issue in graph_data.issues.all()[:5]:
    print(f"{issue.key}: {issue.summary}")

# Find commits by author (reverse index)
from src.common.kernel import EntityRef, EntityKind
# Or scan:
matches = [c for c in graph_data.commits.all()
           if c.author_ref and 'john' in c.author_ref.id.lower()]
print(f"Matches: {len(matches)}")

# Duplication summary
print(graph_data.duplication_summary())

# Files with a specific trait
files = graph_data.find_files_with_trait("hotspot")
print(f"Hotspots: {[f.id for f in files[:10]]}")
```

### 6. Generate Plot

Execute code that creates a matplotlib plot, returns JPEG image. Same
session model as `/execute` — no `project_id` in the path.

```bash
curl -X POST http://localhost:8001/plot \
  -H 'Authorization: Bearer YOUR_JWT_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "code": "commits = graph_data.commits.all()\nauthors = {}\nfor c in commits:\n    a = c.author_ref.id if c.author_ref else \"<unknown>\"\n    authors[a] = authors.get(a, 0) + 1\ntop = sorted(authors.items(), key=lambda x: x[1], reverse=True)[:10]\nnames, counts = zip(*top)\nplt.figure(figsize=(10, 6))\nplt.barh(names, counts)\nplt.xlabel(\"Commits\")\nplt.title(\"Top 10 Contributors\")\nplt.tight_layout()"
  }' --output plot.jpg
```

**Response:** Binary JPEG image

**Available variables:**
- `graph_data`, `graph`, helpers — same as `/execute`
- `plt` — `matplotlib.pyplot` module

### 7. Unload Project

Remove project graph from memory and set status to idle.

```bash
curl -X DELETE http://localhost:8001/projects/PROJECT_UUID/unload \
  -H 'Authorization: Bearer YOUR_JWT_TOKEN'
```

## Error Handling

### Missing Files (400)

```json
{
  "detail": "Missing required files: git, jira"
}
```

### No Project Loaded

`/execute` and `/plot` raise inside the snippet (`graph_data` is `None`).
Call `/projects/{id}/load` or `/projects/{id}/build` first.

### Invalid Token (401)

```json
{
  "detail": "Invalid or expired token: ..."
}
```

### Project Not Found (RLS blocks access)

When you don't have access to a project due to RLS:
- `/build` returns empty files list, then fails validation
- User will get "Missing required files" error

## Testing Workflow

1. **Create Project** (via web-ui)
2. **Upload Files** (via web-ui file service)
   - Git (.iglog file)
   - JIRA (JSON)
   - GitHub (JSON)
   - Optional: Lizard CSV, CodeFrame JSONL, DuDe JSON, Insider JSON
3. **Build Graph** — builds AND loads the project (becomes current):
   ```bash
   curl -X POST http://localhost:8001/projects/PROJECT_ID/build \
     -H "Authorization: Bearer $TOKEN"
   ```
4. **Query Data** — no project_id in path; targets the current project:
   ```bash
   curl -X POST http://localhost:8001/execute \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"code": "print(graph_data)"}'
   ```
5. **Unload When Done**
   ```bash
   curl -X DELETE http://localhost:8001/projects/PROJECT_ID/unload \
     -H "Authorization: Bearer $TOKEN"
   ```

## Data-Server Port

Note: The data-server runs on port **8001** (not 8000) to avoid conflict with Supabase Kong gateway.

Update your uvicorn command:
```bash
uvicorn src.server:app --host 0.0.0.0 --port 8001 --reload
```

## Interactive API Docs

FastAPI automatically generates interactive documentation:

- **Swagger UI**: http://localhost:8001/docs
- **ReDoc**: http://localhost:8001/redoc

Use these to test endpoints interactively in your browser.
