"""MCP sandbox package — façades + helpers for the agent-facing ``/execute`` scope.

See ``architectural_changes.md`` §11 and the chunk 9 handoff for the
mapping spec. Two view classes live here, one per lifecycle stage:

* :class:`SetupSandboxView` — PRE_MERGE / setup-stage surface.
* :class:`QuerySandboxView` — POST_FINALIZE / query-stage surface.
  (Renamed from ``MCPSandboxView``; the old name remains as a
  backwards-compat alias.)

Public surface::

    from src.sandbox import (
        SetupSandboxView, QuerySandboxView,
        MCPSandboxView,  # deprecated alias for QuerySandboxView
        commit_issues, issue_commits, pr_commits,
    )
"""
from __future__ import annotations

from .helpers import commit_issues, issue_commits, pr_commits
from .inject import MCPSandboxView, QuerySandboxView, SetupSandboxView

__all__ = [
    "MCPSandboxView",
    "QuerySandboxView",
    "SetupSandboxView",
    "commit_issues",
    "issue_commits",
    "pr_commits",
]
