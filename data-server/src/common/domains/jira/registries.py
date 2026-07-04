"""Registries for every Jira-domain :class:`Entity` subclass.

Each registry declares the secondary indexes Chunk 7 (relation builders) and
the MCP sandbox helpers (Chunk 11) actually use. Per plan §1.5 and the Chunk
4 git-domain pattern, indexes are declared as a ``ClassVar[list[IndexSpec]]``
and rebuilt on every mutation / on :meth:`Registry.load` — they are NOT
pickled.
"""
from __future__ import annotations

from typing import Optional

from ...kernel import IndexSpec, Registry
from .models import (
    Issue,
    IssueStatus,
    IssueType,
    JiraProject,
    JiraUser,
)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class JiraProjectRegistry(Registry[JiraProject, str]):
    """Holds every :class:`JiraProject` in the graph.

    Same shape as :class:`git.GitProjectRegistry` — domain-specific
    typing helper. At the graph level, all :class:`Project` subclasses
    may share a single :class:`ProjectRegistry` (plan §3 + Chunk 2
    design choice §5); Chunk 8 decides whether to merge.
    """

    indexes = [
        IndexSpec(name="by_name", key_fn=lambda p: p.name, multi=True),
    ]

    def get_id(self, entity: JiraProject) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def _user_unified_key(u: JiraUser) -> Optional[str]:
    """Key function for the ``by_unified_user`` index.

    Returning ``None`` skips the entity for this spec — so an account
    without an accepted smart-merge simply doesn't land in this bucket.
    Mirrors the Chunk 4 ``_account_unified_key`` pattern.
    """
    return u.unified_user_id


class JiraUserRegistry(Registry[JiraUser, str]):
    """All :class:`JiraUser` instances seen across the graph.

    Index choices (plan §4.1 + handoff "Public API"):

    * ``by_key``           — lookup by Jira's per-project user key
                             (the only stable identifier short of the
                             full ``link`` URL).
    * ``by_project``       — every user that touched this project
                             (one bucket per ``project_ref``).
    * ``by_unified_user``  — reverse index for "show me every account
                             already merged into this
                             :class:`UnifiedUser`". ``None`` keys are
                             skipped automatically.
    """

    indexes = [
        IndexSpec(name="by_key", key_fn=lambda u: u.key, multi=True),
        IndexSpec(name="by_project", key_fn=lambda u: u.project_ref, multi=True),
        IndexSpec(name="by_unified_user", key_fn=_user_unified_key, multi=True),
    ]

    def get_id(self, entity: JiraUser) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


class IssueRegistry(Registry[Issue, str]):
    """Every :class:`Issue` seen across the graph.

    Index choices (plan §4.1 + handoff "Public API"):

    * ``by_project``  — one bucket per :class:`JiraProject` ref.
    * ``by_assignee`` — fan-out over :pyattr:`Issue.assignee_refs`. Each
                        assignee :class:`JiraUser` ref gets a bucket
                        containing every issue assigned to them.
    * ``by_reporter`` — single :class:`EntityRef`. ``None`` skipped.
    * ``by_creator``  — single :class:`EntityRef`. ``None`` skipped.
    * ``by_status``   — single :class:`EntityRef` to the current
                        :class:`IssueStatus` ref.
    * ``by_type``     — single :class:`EntityRef` to the
                        :class:`IssueType` ref.
    * ``by_parent``   — replaces legacy ``Issue.children``: each parent
                        ref gets a bucket of its children. ``None``
                        skipped.
    * ``by_key``      — lookup by issue key (same value as ``id``;
                        explicit index so the smart-merge UI's
                        "give me issue X" pattern doesn't have to
                        round-trip through the primary id).
    """

    indexes = [
        IndexSpec(name="by_project", key_fn=lambda i: i.project_ref, multi=True),
        IndexSpec(name="by_assignee", key_fn=lambda i: i.assignee_refs, multi=True),
        IndexSpec(name="by_reporter", key_fn=lambda i: i.reporter_ref, multi=True),
        IndexSpec(name="by_creator", key_fn=lambda i: i.creator_ref, multi=True),
        IndexSpec(name="by_status", key_fn=lambda i: i.status_ref, multi=True),
        IndexSpec(name="by_type", key_fn=lambda i: i.type_ref, multi=True),
        IndexSpec(name="by_parent", key_fn=lambda i: i.parent_ref, multi=True),
        IndexSpec(name="by_key", key_fn=lambda i: i.key, multi=True),
    ]

    def get_id(self, entity: Issue) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Issue statuses
# ---------------------------------------------------------------------------


class IssueStatusRegistry(Registry[IssueStatus, str]):
    """Every :class:`IssueStatus` seen across the graph.

    Index choices:

    * ``by_project``  — one bucket per :class:`JiraProject` ref.
    * ``by_name``     — lookup by the human-readable name (the metric
                        layer uses this when computing "stalled issues",
                        which keys on the status name not the id).
    * ``by_category`` — group by category (``"new"`` / ``"indeterminate"``
                        / ``"done"``) for high-level lifecycle queries.
    """

    indexes = [
        IndexSpec(name="by_project", key_fn=lambda s: s.project_ref, multi=True),
        IndexSpec(name="by_name", key_fn=lambda s: s.name, multi=True),
        IndexSpec(name="by_category", key_fn=lambda s: s.category, multi=True),
    ]

    def get_id(self, entity: IssueStatus) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Issue types
# ---------------------------------------------------------------------------


class IssueTypeRegistry(Registry[IssueType, str]):
    """Every :class:`IssueType` seen across the graph.

    Index choices:

    * ``by_project`` — one bucket per :class:`JiraProject` ref.
    * ``by_name``    — lookup by the type name (``"Bug"`` / ``"Story"`` /
                       …). Metrics that classify "issues of kind Bug"
                       use this rather than the opaque id.
    """

    indexes = [
        IndexSpec(name="by_project", key_fn=lambda t: t.project_ref, multi=True),
        IndexSpec(name="by_name", key_fn=lambda t: t.name, multi=True),
    ]

    def get_id(self, entity: IssueType) -> str:
        return entity.id


__all__ = [
    "IssueRegistry",
    "IssueStatusRegistry",
    "IssueTypeRegistry",
    "JiraProjectRegistry",
    "JiraUserRegistry",
]
