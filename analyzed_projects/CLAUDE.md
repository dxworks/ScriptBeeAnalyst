# ScriptBee - Project Analysis Workspace

You are analyzing software project data loaded in a FastAPI data server.
The data consists of Git commit history, JIRA issues, and GitHub pull requests,
all linked together in an in-memory graph structure.

## MCP Tools Available

You have 8 tools from the `scriptbee-data` MCP server:

| Tool | Purpose |
|------|---------|
| `list_metrics` | Live catalog of every classifier, anomaly trait, relation kind, and overview table — reflects source code, no staleness. Call this once per session before exploring metrics. |
| `execute_code` | Run Python code against the project graph. Use `print()` for output. |
| `generate_plot` | Create matplotlib visualizations. `plt` is pre-imported. Don't call `plt.show()`. |
| `get_project_status` | Check if a project is loaded and get statistics. |
| `load_project` | Load a project by its UUID (shown in the web UI). |
| `list_anomalies` | Filter enrichment tags by trait_name and/or entity_kind. |
| `get_overview_table` | Fetch an overview table by name as parsed CSV rows. |
| `get_relation_edges` | Fetch a relation file as edges, sorted by strength. |

## Workflow

1. **Check status**: call `get_project_status` to see if a project is loaded
2. **Load if needed**: call `load_project` with the project UUID
3. **Discover what's available**: call `list_metrics` to see the live catalog
4. **Query data**: write Python code using `graph_data` dict and call `execute_code`
5. **Visualize**: write matplotlib code and call `generate_plot`

## Quick Reference

```python
# Access the three data sources
git_project    = graph_data['git']     # commits, files, changes, authors
jira_project   = graph_data['jira']    # issues, statuses, types, users
github_project = graph_data['github']  # pull requests, users, commits
unified_users  = graph_data.get('users', [])  # merged identities (after setup)

# Iterate all entities via registries
for commit in git_project.git_commit_registry.all:
    print(commit.id[:8], commit.message[:50])

# Lookup by ID
commit = git_project.git_commit_registry.get_by_id("abc123")
issue = jira_project.issue_registry.get_by_id("PROJ-42")
pr = github_project.pull_request_registry.get_by_id(17)

# Cross-project navigation
commit.issues          # JIRA issues linked to this commit
commit.pull_requests   # GitHub PRs containing this commit
issue.git_commits      # Git commits mentioning this issue
pr.git_commits         # Git commits in this PR
```

## Detailed Documentation

**Source-of-truth principle**: metric definitions live in source code, not in
this file. Use `list_metrics` to discover what exists, then `Read` the
`source_file` it returns for the computational rule. Threshold values live in
`data-server/src/enrichment/config.py` (`EnrichmentConfig`).

See the `instructions/` folder for the entry-point and pattern recipes:

- `compass.md` — what kind of agent you are, where things live, how to answer
  metric questions. Read this first.
- `query-examples.txt` — worked-example queries against the graph and
  enrichment layer.
- `plot-patterns.txt` — matplotlib visualization patterns.
