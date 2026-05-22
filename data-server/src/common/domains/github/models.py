"""GitHub-domain entities for the v2 graph.

Faithful port of ``src/common/github_models.py`` (legacy). Every
cross-entity reference uses :class:`EntityRef`, never a Python object
reference — per plan §4.

Entity-vs-value-object decisions:

* :class:`PullRequest`, :class:`Review`, :class:`ReviewComment`,
  :class:`GitHubCommit`, :class:`GitHubUser`, :class:`GitHubProject` are
  all real :class:`Entity` subclasses (plan §1.1 + §4.1).
* Each has a kernel :class:`EntityKind` member, so unlike the Jira
  ``IssueTransition`` situation, no kernel enum changes are needed.
* Plan §4 highlights that the GitHub ``GitHubCommit`` is intentionally
  kept distinct from the git domain's :class:`git.Commit` — the
  GitHub-side metadata (PR association, GitHub-API-provided sha+date) is
  different per record, and the two never collide on the wire.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, List, Optional

from ...kernel import Entity, EntityKind, EntityRef
from ...people import Account, SourceKind
from ...projects import Project

if TYPE_CHECKING:  # forward-only — keeps cycles broken
    from .transformer import GitHubTransformer  # noqa: F401


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class GitHubProject(Project):
    """A single GitHub repository's metadata.

    Per plan §3 the Project is metadata only — entities live in flat
    registries on :class:`Graph`. The legacy ``GitHubProject`` owned three
    registries (user / pull-request / commit); that ownership moves to
    :class:`Graph` in Chunk 8.
    """

    kind: ClassVar[EntityKind] = EntityKind.PROJECT

    def transformer_class(self) -> type["GitHubTransformer"]:  # type: ignore[override]
        # Lazy import — same pattern as :class:`git.GitProject` /
        # :class:`jira.JiraProject`.
        from .transformer import GitHubTransformer

        return GitHubTransformer


class GitHubUser(Account):
    """A GitHub user account.

    Field mapping vs legacy ``github_models.GitHubUser``:

    * ``id``              — was the legacy ``url`` field (used as registry
                            id). v2 uses the same value as the canonical
                            :class:`Entity.id` so cross-refs stay stable
                            across re-ingests.
    * ``login``           — was ``login: Optional[str]`` (the GitHub
                            handle). The legacy DTO made it optional
                            because repo-owner records sometimes lacked
                            it; we preserve that here.
    * ``name``            — inherited from :class:`Account`.
    * ``url``             — preserved (the user-facing URL — same value
                            as ``id`` but kept as a named field because
                            consumers reach for ``user.url``).
    * ``project_ref``     — was implicit through ``GitHubProject``'s
                            ``git_hub_user_registry`` ownership; now a
                            typed ref.
    * ``unified_user_id`` — inherited from :class:`Account`.
    * ``pull_requests_as_*``
                          — DROPPED. Reverse lookup via
                            :class:`PullRequestRegistry.by_author` /
                            ``by_merged_by`` (the assignee link can
                            stay a Relation when Chunk 7 needs it).

    Resolver methods (auto-generated, see ``kernel/entity.py``):
        ``.project(graph)`` -> ``GitHubProject | None``
    """

    kind: ClassVar[EntityKind] = EntityKind.GITHUB_USER
    # ``source`` is declared on :class:`Account` as an abstract property.
    # ClassVar override matches the Chunk 4 / Chunk 5 git-account and
    # jira-user pattern.
    source: ClassVar[SourceKind] = SourceKind.GITHUB  # type: ignore[misc]

    login: Optional[str] = None
    url: Optional[str] = None


class PullRequest(Entity):
    """A pull request — the top-level GitHub review unit.

    Field mapping vs legacy ``github_models.PullRequest``:

    * ``id``                          — was the legacy registry id, which
                                        was ``number: int``. v2 uses a
                                        string id (the PR number as a
                                        string) so the global
                                        :class:`EntityRef` shape stays
                                        ``(kind, str)``.
    * ``number``                      — preserved (the integer PR number;
                                        the int form remains the cheap
                                        composite for the
                                        :class:`PullRequestRegistry.by_number`
                                        index).
    * ``project_ref``                 — NEW: typed ref to
                                        :class:`GitHubProject`.
    * ``title`` / ``body`` /
      ``state`` / ``changed_files``   — preserved.
    * ``created_at`` / ``updated_at`` /
      ``merged_at`` / ``closed_at``   — preserved (snake-case'd; legacy
                                        carried camelCase wire-format
                                        names).
    * ``author_ref``                  — was ``createdBy: Optional[GitHubUser]``;
                                        we name it ``author`` because the
                                        review-system literature uses that
                                        consistently and it pairs nicely
                                        with :class:`Review.author_ref`.
    * ``merged_by_ref``               — was ``mergedBy: Optional[GitHubUser]``.
    * ``assignee_refs`` /
      ``requested_reviewer_refs``     — were ``assignees`` /
                                        ``requestedReviewers``: List
                                        flatten to typed refs.
    * ``commit_refs``                 — was ``git_hub_commits:
                                        List[GitHubCommit]``. Plan §4
                                        explicitly lists this rename
                                        (``PullRequest.commit_refs``).
    * ``review_refs``                 — was ``reviews: List[Review]``. v2
                                        carries typed refs; the reverse
                                        index on :class:`ReviewRegistry`
                                        also works for "all reviews on PR
                                        X".
    * ``review_comment_refs``         — was ``reviewComments:
                                        List[ReviewComment]``. Same shape
                                        as ``review_refs``.
    * ``issues`` / ``git_commits``    — DROPPED. Cross-source links move
                                        into :class:`RelationRegistry`
                                        (Chunk 7).

    Resolver methods (auto-generated, see ``kernel/entity.py``):
        ``.project(graph)``             -> ``GitHubProject | None``
        ``.author(graph)``              -> ``GitHubUser | None``
        ``.merged_by(graph)``           -> ``GitHubUser | None``
        ``.assignees(graph)``           -> ``list[GitHubUser]``
        ``.requested_reviewers(graph)`` -> ``list[GitHubUser]``
        ``.commits(graph)``             -> ``list[GitHubCommit]``
        ``.reviews(graph)``             -> ``list[Review]``
        ``.review_comments(graph)``     -> ``list[ReviewComment]``
    """

    kind: ClassVar[EntityKind] = EntityKind.PULL_REQUEST

    project_ref: EntityRef
    number: int
    title: str
    body: str = ""
    state: str
    changed_files: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None
    merged_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    author_ref: Optional[EntityRef] = None
    merged_by_ref: Optional[EntityRef] = None
    assignee_refs: List[EntityRef] = []
    requested_reviewer_refs: List[EntityRef] = []
    commit_refs: List[EntityRef] = []
    review_refs: List[EntityRef] = []
    review_comment_refs: List[EntityRef] = []

    @staticmethod
    def make_id(number: int) -> str:
        """Stable string id from the PR number (the only natural id GitHub
        guarantees within a single repo)."""
        return str(number)


class Review(Entity):
    """A single review on a :class:`PullRequest`.

    Field mapping vs legacy ``github_models.Review``:

    * ``id``                  — NEW: GitHub doesn't expose a stable
                                review id on the legacy DTO; we use the
                                composite ``f"{pr_number}#{ordinal}"``
                                produced by
                                :meth:`Review.make_id`. The transformer
                                assigns ordinals in the order GitHub
                                returned them.
    * ``pull_request_ref``    — NEW: typed ref to the parent
                                :class:`PullRequest`.
    * ``ordinal``             — NEW: position within the PR's review
                                stream (composability with the id helper).
    * ``state``               — unchanged (``APPROVED`` /
                                ``CHANGES_REQUESTED`` / ``COMMENTED`` /
                                ``DISMISSED`` …).
    * ``submitted_at``        — was ``submittedAt: Optional[datetime]``
                                (snake-case'd).
    * ``body``                — unchanged.
    * ``author_ref``          — was ``user: Optional[GitHubUser]``.
    * ``review_comment_refs`` — NEW: typed refs to the inline
                                :class:`ReviewComment` entities (legacy
                                stored them inline on the review and the
                                transformer flattened them onto the PR).

    Plan §1.1 lists ``EntityKind.REVIEW``, so reviews are first-class
    entities (and Chunk 7's relation builders can attach traits to
    specific reviews).

    Resolver methods (auto-generated, see ``kernel/entity.py``):
        ``.pull_request(graph)``    -> ``PullRequest | None``
        ``.author(graph)``          -> ``GitHubUser | None``
        ``.review_comments(graph)`` -> ``list[ReviewComment]``
    """

    kind: ClassVar[EntityKind] = EntityKind.REVIEW

    pull_request_ref: EntityRef
    ordinal: int
    state: str
    body: str = ""
    submitted_at: Optional[datetime] = None
    author_ref: Optional[EntityRef] = None
    review_comment_refs: List[EntityRef] = []

    @staticmethod
    def make_id(pr_number: int, ordinal: int) -> str:
        return f"{pr_number}#{ordinal}"


class ReviewComment(Entity):
    """An inline code-review comment.

    Field mapping vs legacy ``github_models.ReviewComment``:

    * ``id``               — was the legacy registry id, which was the
                             review-comment's GitHub ``url`` (the
                             ``discussion_r/...`` URL). v2 keeps that
                             choice — the URL is stable across
                             re-ingests.
    * ``review_ref``       — NEW: typed ref to the owning
                             :class:`Review`. Legacy attached comments
                             to the PR via the transformer's
                             ``reviewComments`` flattening; v2 keeps the
                             link to the originating review so Chunk 7
                             can correlate "approval comments" vs
                             "blocking comments".
    * ``pull_request_ref`` — NEW: redundant with the review's PR but
                             carried explicitly for cheap "all comments
                             in PR X" indexing without a Join through
                             :class:`ReviewRegistry`.
    * ``url``              — preserved (same value as ``id``; consumers
                             reach for ``comment.url``).
    * ``body``             — unchanged.
    * ``created_at`` /
      ``updated_at``       — preserved (snake-case'd).
    * ``author_ref``       — was ``author: Optional[GitHubUser]``.
    * ``file_path`` /
      ``line``             — NEW optional fields; the legacy DTO didn't
                             carry them on every record, but the
                             plan-§4 entity list suggests they exist
                             where the GitHub API exposes them. Default
                             ``None`` keeps backwards compat.

    Resolver methods (auto-generated, see ``kernel/entity.py``):
        ``.review(graph)``       -> ``Review | None``
        ``.pull_request(graph)`` -> ``PullRequest | None``
        ``.author(graph)``       -> ``GitHubUser | None``
    """

    kind: ClassVar[EntityKind] = EntityKind.REVIEW_COMMENT

    review_ref: EntityRef
    pull_request_ref: EntityRef
    url: str
    body: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    author_ref: Optional[EntityRef] = None
    file_path: Optional[str] = None
    line: Optional[int] = None


class GitHubCommit(Entity):
    """A commit as seen by the GitHub API.

    Kept distinct from the git domain's :class:`git.Commit` because the
    GitHub-side metadata is different (PR association, GitHub-API date,
    changed-file count) and the two never collide on the wire — plan §4.

    Field mapping vs legacy ``github_models.GitHubCommit``:

    * ``id``                  — was the SHA (the legacy registry id).
                                v2 keeps the SHA as the canonical
                                :class:`Entity.id`.
    * ``sha``                 — preserved (same value as ``id``; this
                                is what relation-builders index on
                                when joining to :class:`git.Commit`
                                via :class:`GitHubCommitRegistry.by_sha`).
    * ``pull_request_ref``    — NEW: typed ref to the owning
                                :class:`PullRequest`. The legacy DTO
                                grouped commits under the PR; v2 keeps
                                a single ref so cross-PR commits are
                                unambiguous.
    * ``date``                — preserved (GitHub's authored-date).
    * ``message``             — unchanged.
    * ``changed_files``       — preserved (snake-case'd; was
                                ``changedFiles``).
    * ``author_ref``          — NEW: typed ref to the originating
                                :class:`GitHubUser` (Chunk 7's relation
                                builder can promote this into a
                                cross-source link).
    * ``url``                 — preserved (the GitHub-side commit URL).
    * ``pull_requests``       — DROPPED (was the back-pointer list).
                                Reverse lookup via
                                :class:`GitHubCommitRegistry.by_pull_request`.

    Resolver methods (auto-generated, see ``kernel/entity.py``):
        ``.pull_request(graph)`` -> ``PullRequest | None``
        ``.author(graph)``       -> ``GitHubUser | None``
    """

    kind: ClassVar[EntityKind] = EntityKind.GITHUB_COMMIT

    pull_request_ref: EntityRef
    sha: str
    date: datetime
    message: str
    changed_files: int = 0
    author_ref: Optional[EntityRef] = None
    url: Optional[str] = None


__all__ = [
    "GitHubCommit",
    "GitHubProject",
    "GitHubUser",
    "PullRequest",
    "Review",
    "ReviewComment",
]
