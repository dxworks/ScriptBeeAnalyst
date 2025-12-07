# Data Server API Testing Guide

This guide demonstrates how to test the new Supabase-integrated API endpoints.

## Prerequisites

1. Supabase running locally (Docker)
2. Data-server running: `uvicorn src.server:app --host 0.0.0.0 --port 8001 --reload`
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

Extract the `access_token` from the response - this is your JWT.

## API Endpoints

### 1. Health Check (No Auth)

```bash
curl -X GET http://localhost:8001/health
```

**Response:**
```json
{
  "status": "ok",
  "loaded_projects": []
}
```

### 2. Build Project Graph

Load a project's files from Supabase and build the graph in memory.

```bash
curl -X POST http://localhost:8001/projects/PROJECT_UUID/build \
  -H 'Authorization: Bearer YOUR_JWT_TOKEN' \
  -H 'Content-Type: application/json'
```

**Response:**
```json
{
  "message": "Project loaded successfully",
  "project_id": "abc-123-def",
  "status": "ready"
}
```

**Status Flow:**
- `draft` → `processing` (during build) → `ready` (success) or `error` (failure)

### 3. Execute Code

Run Python code against the loaded graph.

```bash
curl -X POST http://localhost:8001/projects/PROJECT_UUID/execute \
  -H 'Authorization: Bearer YOUR_JWT_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "code": "print(f\"Total commits: {len(graph_data[\"git\"].git_commit_registry.all)}\")"
  }'
```

**Response:**
```json
{
  "output": "Total commits: 5634\n"
}
```

**Available Variables:**
- `graph_data`: Dict with keys `'git'`, `'jira'`, `'github'`
  - `graph_data['git']`: GitProject instance
  - `graph_data['jira']`: JiraProject instance
  - `graph_data['github']`: GitHubProject instance

**Example Code Snippets:**

```python
# Count commits
print(len(graph_data['git'].git_commit_registry.all))

# List JIRA issues
for issue in graph_data['jira'].issue_registry.all[:5]:
    print(f"{issue.key}: {issue.summary}")

# Find commits by author
commits = [c for c in graph_data['git'].git_commit_registry.all
           if 'john' in c.author.name.lower()]
print(f"John's commits: {len(commits)}")
```

### 4. Generate Plot

Execute code that creates a matplotlib plot, returns JPEG image.

```bash
curl -X POST http://localhost:8001/projects/PROJECT_UUID/plot \
  -H 'Authorization: Bearer YOUR_JWT_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "code": "commits = graph_data[\"git\"].git_commit_registry.all\nauthors = {}\nfor c in commits:\n    author = c.author.name\n    authors[author] = authors.get(author, 0) + 1\ntop_authors = sorted(authors.items(), key=lambda x: x[1], reverse=True)[:10]\nnames, counts = zip(*top_authors)\nplt.figure(figsize=(10, 6))\nplt.barh(names, counts)\nplt.xlabel(\"Commits\")\nplt.title(\"Top 10 Contributors\")\nplt.tight_layout()"
  }' --output plot.jpg
```

**Response:** Binary JPEG image

**Available Variables:**
- `graph_data`: Same as /execute
- `plt`: matplotlib.pyplot module

### 5. Unload Project

Remove project graph from memory and set status to 'idle'.

```bash
curl -X DELETE http://localhost:8001/projects/PROJECT_UUID/unload \
  -H 'Authorization: Bearer YOUR_JWT_TOKEN'
```

**Response:**
```json
{
  "message": "Project unloaded successfully",
  "project_id": "abc-123-def"
}
```

## Error Handling

### Missing Files (400)

```json
{
  "detail": "Missing required files: git, jira"
}
```

### Graph Not Loaded (400)

```json
{
  "detail": "Graph not loaded. Call /projects/{project_id}/build first"
}
```

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
3. **Build Graph** (via API or web-ui trigger)
   ```bash
   curl -X POST http://localhost:8001/projects/PROJECT_ID/build \
     -H "Authorization: Bearer $TOKEN"
   ```
4. **Query Data**
   ```bash
   curl -X POST http://localhost:8001/projects/PROJECT_ID/execute \
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
