"""Registries for every GitHub-domain :class:`Entity` subclass.

Each registry declares the secondary indexes Chunk 7 (relation builders) and
the MCP sandbox helpers (Chunk 11) actually use. Per plan §1.5 and the Chunk
4/5 patterns, indexes are declared as a ``ClassVar[list[IndexSpec]]`` and
rebuilt on every mutation / on :meth:`Registry.load` — they are NOT pickled.
"""
from __future__ import annotations

from typing import Optional

from ...kernel import IndexSpec, Registry
from .models import (
    GitHubCommit,
    GitHubProject,
    GitHubUser,
    PullRequest,
    Review,
    ReviewComment,
)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class GitHubProjectRegistry(Registry[GitHubProject, str]):
    """Holds every :class:`GitHubProject` in the graph.

    Same shape as :class:`git.GitProjectRegistry` /
    :class:`jira.JiraProjectRegistry` — a domain-specific typing helper
    that Chunk 8 may keep or merge into the shared
    :class:`ProjectRegistry`.
    """

    indexes = [
        IndexSpec(name="by_name", key_fn=lambda p: p.name, multi=True),
    ]

    def get_id(self, entity: GitHubProject) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def _user_login_key(u: GitHubUser) -> Optional[str]:
    """Key function for the ``by_login`` index.

    GitHub users sometimes lack a login (repo-owner placeholder records
    are an example documented in the legacy DTO). Returning ``None``
    skips the entity for this index, matching the kernel
    ``_normalize_keys`` semantics.
    """
    return u.login


def _user_unified_key(u: GitHubUser) -> Optional[str]:
    return u.unified_user_id


class GitHubUserRegistry(Registry[GitHubUser, str]):
    """All :class:`GitHubUser` instances seen across the graph.

    Index choices (plan §4.1 + handoff "Public API"):

    * ``by_login``        — lookup by the GitHub handle. ``None`` keys
                            (records that lack a login) are skipped.
    * ``by_project``      — every user that touched this project
                            (one bucket per ``project_ref``).
    * ``by_unified_user`` — reverse index for "show me every account
                            already merged into this
                            :class:`UnifiedUser`". ``None`` skipped.
    """

    indexes = [
        IndexSpec(name="by_login", key_fn=_user_login_key, multi=True),
        IndexSpec(name="by_project", key_fn=lambda u: u.project_ref, multi=True),
        IndexSpec(name="by_unified_user", key_fn=_user_unified_key, multi=True),
    ]

    def get_id(self, entity: GitHubUser) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Pull requests
# ---------------------------------------------------------------------------


class PullRequestRegistry(Registry[PullRequest, str]):
    """Every :class:`PullRequest` seen across the graph.

    Index choices (plan §4.1 + handoff "Public API"):

    * ``by_project``  — one bucket per :class:`GitHubProject` ref.
    * ``by_author``   — :class:`EntityRef` to :class:`GitHubUser`.
                        ``None`` (anonymous / unauthored) is skipped.
    * ``by_state``    — group by PR state (``open`` / ``closed`` /
                        ``merged``) for lifecycle metrics.
    * ``by_number``   — lookup by integer PR number (the natural id
                        people quote). Indexed as an int so the
                        MCP sandbox doesn't have to cast.
    * ``by_merged_by`` — :class:`EntityRef` to the :class:`GitHubUser`
                         that merged the PR. ``None`` (still open /
                         unmerged) is skipped by the kernel's
                         ``_normalize_keys``.
    * ``by_assignee`` — fan-out over :pyattr:`PullRequest.assignee_refs`.
                         Each assignee ref gets a bucket containing every
                         PR assigned to them. Mirrors
                         :class:`IssueRegistry.by_assignee`.
    * ``by_requested_reviewer`` — fan-out over
                         :pyattr:`PullRequest.requested_reviewer_refs`.
                         Each reviewer ref gets a bucket containing every
                         PR they were requested to review. Same plural
                         shape as ``by_assignee``.
    """

    indexes = [
        IndexSpec(name="by_project", key_fn=lambda p: p.project_ref, multi=True),
        IndexSpec(name="by_author", key_fn=lambda p: p.author_ref, multi=True),
        IndexSpec(name="by_state", key_fn=lambda p: p.state, multi=True),
        IndexSpec(name="by_number", key_fn=lambda p: p.number, multi=True),
        IndexSpec(name="by_merged_by", key_fn=lambda p: p.merged_by_ref, multi=True),
        IndexSpec(name="by_assignee", key_fn=lambda p: p.assignee_refs, multi=True),
        IndexSpec(name="by_requested_reviewer", key_fn=lambda p: p.requested_reviewer_refs, multi=True),
    ]

    def get_id(self, entity: PullRequest) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------


class ReviewRegistry(Registry[Review, str]):
    """Every :class:`Review` seen across the graph.

    Index choices (plan §4.1 + handoff "Public API"):

    * ``by_pull_request`` — every review on PR X.
    * ``by_author``       — single :class:`EntityRef`. ``None`` skipped.
    * ``by_state``        — quick "all APPROVED / CHANGES_REQUESTED"
                            for lifecycle metrics (StalledReview etc.)
    """

    indexes = [
        IndexSpec(
            name="by_pull_request", key_fn=lambda r: r.pull_request_ref, multi=True
        ),
        IndexSpec(name="by_author", key_fn=lambda r: r.author_ref, multi=True),
        IndexSpec(name="by_state", key_fn=lambda r: r.state, multi=True),
    ]

    def get_id(self, entity: Review) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Review comments
# ---------------------------------------------------------------------------


class ReviewCommentRegistry(Registry[ReviewComment, str]):
    """Every :class:`ReviewComment` seen across the graph.

    Index choices (plan §4.1):

    * ``by_review``       — every comment on review R.
    * ``by_pull_request`` — every comment in PR X (cheaper than joining
                            through reviews).
    * ``by_author``       — :class:`EntityRef`. ``None`` skipped.
    * ``by_file``         — group by ``file_path`` for "comments on
                            this file across history". ``None`` skipped
                            because the legacy DTO didn't always carry
                            the file path.
    """

    indexes = [
        IndexSpec(name="by_review", key_fn=lambda c: c.review_ref, multi=True),
        IndexSpec(
            name="by_pull_request", key_fn=lambda c: c.pull_request_ref, multi=True
        ),
        IndexSpec(name="by_author", key_fn=lambda c: c.author_ref, multi=True),
        IndexSpec(name="by_file", key_fn=lambda c: c.file_path, multi=True),
    ]

    def get_id(self, entity: ReviewComment) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# GitHub commits
# ---------------------------------------------------------------------------


class GitHubCommitRegistry(Registry[GitHubCommit, str]):
    """Every :class:`GitHubCommit` seen across the graph.

    Index choices (plan §4.1 + handoff "Public API"):

    * ``by_pull_request`` — fast "all commits in PR X".
    * ``by_author``       — :class:`EntityRef`. ``None`` skipped.
    * ``by_sha``          — lookup by SHA string (same value as ``id``
                            but indexed explicitly because Chunk 7's
                            cross-source linker keys git ↔ github
                            commits on this field, not on the registry
                            primary id).
    """

    indexes = [
        IndexSpec(
            name="by_pull_request", key_fn=lambda c: c.pull_request_ref, multi=True
        ),
        IndexSpec(name="by_author", key_fn=lambda c: c.author_ref, multi=True),
        IndexSpec(name="by_sha", key_fn=lambda c: c.sha, multi=True),
    ]

    def get_id(self, entity: GitHubCommit) -> str:
        return entity.id


__all__ = [
    "GitHubCommitRegistry",
    "GitHubProjectRegistry",
    "GitHubUserRegistry",
    "PullRequestRegistry",
    "ReviewCommentRegistry",
    "ReviewRegistry",
]
