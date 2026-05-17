# ScriptBee - Project Analysis Workspace

You are analyzing software project data loaded in a FastAPI data server.
The data consists of Git commit history, JIRA issues, and GitHub pull requests,
all linked together in an in-memory graph structure.

## MCP Tools Available

You have 12 tools from the `scriptbee-data` MCP server. The four
`list_metrics` / `list_file_metrics` / `get_code_structure_summary` /
`get_duplication_summary` tools route through `/execute` against
`MCPSandboxView` (per the Chunk 20 rewrite — the legacy
`/enrichments/*` REST endpoints were deleted in Chunk 10 and are NOT
restored). Every tool below is live.

| Tool | Purpose |
|------|---------|
| `list_metrics` | Live catalog of every Metric: name, family, emits_traits, emits_classifiers, emits_relations, config_fields, plus the registered overview names. Reflects source code, no staleness. Call this once per session before exploring metrics. |
| `execute_code` | Run Python code against the project graph. Use `print()` for output. |
| `generate_plot` | Create matplotlib visualizations. `plt` is pre-imported. Don't call `plt.show()`. |
| `get_project_status` | Check if a project is loaded and get statistics. |
| `load_project` | Load a project by its UUID (shown in the web UI). |
| `list_anomalies` | Filter enrichment tags by trait_name and/or entity_kind. |
| `get_overview_table` | Fetch an overview table by name as parsed CSV rows. All 11 overviews live as of Chunk 18 (see "Live overview tables" below). |
| `get_relation_edges` | Fetch a relation file as edges, sorted by strength. |
| `list_file_metrics` | Per-file Lizard rollup (sum_nloc, max_ccn, avg_ccn, function_count, longest_function_nloc), sorted by sum_nloc desc. Empty dict when no Lizard CSV was ingested. |
| `get_code_structure_summary` | Per-project counts from the JaFax / Codeframe (B2) layer (types, methods, fields, refs). `loaded=False` when no code-structure project exists. |
| `get_duplication_summary` | Per-project bucket counts (external / sibling / internal) from the DuDe (B3) layer. `loaded=False` when no DuDe ingest happened. |
| `get_quality_issues_summary` | Insider (B4) code-smell summary (counts, distinct rules, top rules). |

### Live overview tables

All 11 overviews can be fetched via `get_overview_table(name)` or
`graph_data.overview_as_dict(name)` in `execute_code`:

`authorship`, `code_quality`, `components`, `feature_encapsulation`,
`feature_traceability`, `intent_impact`, `knowledge`, `nature`,
`pace`, `pr_lifecycle`, `testing`.

## Workflow

1. **Check status**: call `get_project_status` to see if a project is loaded
2. **Load if needed**: call `load_project` with the project UUID
3. **Discover what's available**: call `list_metrics` to see the live catalog
4. **Query data**: write Python code using `graph_data` dict and call `execute_code`
5. **Visualize**: write matplotlib code and call `generate_plot`

## Quick Reference

```python
# `graph_data` is a single MCPSandboxView over the typed v2 Graph.
# The four main entity surfaces are exposed directly as registries —
# no more dict-of-projects.

# Iterate all entities via registries
for commit in graph_data.commits.all():
    print(commit.id[:8], commit.message[:50])

# Lookup by ID
commit = graph_data.commits.get("abc123")
issue  = graph_data.issues.get("PROJ-42")
pr     = graph_data.pull_requests.get("17")     # PR ids are strings in v2
file   = graph_data.files.get("src/app.py")

# Cross-entity navigation — entities are sealed data models, so the
# legacy `commit.issues` shape moved to free helper functions that
# read off the typed Graph (also pre-injected into the sandbox):
issues_for_commit   = commit_issues(commit, graph_data)   # was commit.issues
commits_for_pr      = pr_commits(pr, graph_data)          # was pr.git_commits
commits_for_issue   = issue_commits(issue, graph_data)    # was issue.git_commits

# Enrichment side — traits / classifiers / relations live in the typed
# Graph (graph_data.traits / .classifiers / .relations / .components).
from src.common.kernel import EntityKind, EntityRef
file_ref = EntityRef(kind=EntityKind.FILE, id="src/app.py")
tags     = graph_data.tags_for(file_ref)                  # traits + classifiers
bug_files = graph_data.find_files_with_trait("anomaly.testing.BugMagnet")
prod_files = graph_data.find_files_with_classifier("role", "production")
neighbors = graph_data.cochange_neighbors("src/app.py", "lifetime", limit=10)
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
