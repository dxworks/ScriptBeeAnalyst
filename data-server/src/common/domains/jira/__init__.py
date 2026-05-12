"""Jira-domain v2 entities, registries, and transformer.

See plan §4 (Source-domain entities) and §9 (Recipe — adding a new data
source). The public surface here is intentionally narrow: every concrete
class downstream code (Chunks 7/8 + the MCP sandbox) needs is re-exported.
"""
from __future__ import annotations

from .models import (
    Comment,
    Issue,
    IssueStatus,
    IssueTransition,
    IssueType,
    JiraProject,
    JiraUser,
    TransitionItem,
)
from .registries import (
    IssueRegistry,
    IssueStatusRegistry,
    IssueTypeRegistry,
    JiraProjectRegistry,
    JiraUserRegistry,
)
from .transformer import JiraTransformer

__all__ = [
    # models — entities
    "Issue",
    "IssueStatus",
    "IssueType",
    "JiraProject",
    "JiraUser",
    # models — value objects
    "Comment",
    "IssueTransition",
    "TransitionItem",
    # registries
    "IssueRegistry",
    "IssueStatusRegistry",
    "IssueTypeRegistry",
    "JiraProjectRegistry",
    "JiraUserRegistry",
    # transformer
    "JiraTransformer",
]
