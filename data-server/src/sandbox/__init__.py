"""MCP sandbox package — façade + helpers for the agent-facing ``/execute`` scope.

See ``architectural_changes.md`` §11 and the chunk 9 handoff for the
mapping spec. Public surface::

    from src.sandbox import (
        MCPSandboxView,
        commit_issues, issue_commits, pr_commits,
    )
"""
from __future__ import annotations

from .helpers import commit_issues, issue_commits, pr_commits
from .inject import MCPSandboxView

__all__ = [
    "MCPSandboxView",
    "commit_issues",
    "issue_commits",
    "pr_commits",
]
