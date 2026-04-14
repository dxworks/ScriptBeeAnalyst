# ScriptBee - Project Analysis Workspace

You are analyzing software project data loaded in a FastAPI data server.
The data consists of Git commit history, JIRA issues, and GitHub pull requests,
all linked together in an in-memory graph structure.

## MCP Tools Available

You have 4 tools from the `scriptbee-data` MCP server:

| Tool | Purpose |
|------|---------|
| `execute_code` | Run Python code against the project graph. Use `print()` for output. |
| `generate_plot` | Create matplotlib visualizations. `plt` is pre-imported. Don't call `plt.show()`. |
| `get_project_status` | Check if a project is loaded and get statistics. |
| `load_project` | Load a project by its UUID (shown in the web UI). |

## Workflow

1. **Check status**: call `get_project_status` to see if a project is loaded
2. **Load if needed**: call `load_project` with the project UUID
3. **Query data**: write Python code using `graph_data` dict and call `execute_code`
4. **Visualize**: write matplotlib code and call `generate_plot`

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

The data model source code is in `data-server/src/common/`:
- `base_models.py` - Base classes (Project, Account, Developer)
- `git_models.py` - Git domain (GitProject, GitCommit, File, Change, etc.)
- `jira_models.py` - JIRA domain (JiraProject, Issue, IssueStatus, etc.)
- `github_models.py` - GitHub domain (GitHubProject, PullRequest, GitHubCommit, etc.)
- `registries.py` - AbstractRegistry base class
- `project_linkers.py` - Cross-project relationship linking

See `instructions/` folder for usage guides:
- `guide.txt` - Graph structure, cross-project links, and rules
- `query-examples.txt` - 6 example queries with full Python code
- `plot-patterns.txt` - Matplotlib visualization patterns
