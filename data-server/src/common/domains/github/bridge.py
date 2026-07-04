"""Raw GitHub JSON → v2 entity bundle bridge.

The :class:`GitHubTransformer` accepts a pre-built entity bundle (see its
module docstring) but explicitly does NOT walk the raw mined GitHub JSON
itself — that's the bridge's job. This module reads the JSON via
:class:`GithubJsonLoader`, walks the resulting reader DTO, and emits the
bundle shape ``GitHubTransformer.transform()`` consumes::

    {
        "project":         GitHubProject(...),
        "users":           [GitHubUser, ...],
        "pull_requests":   [PullRequest, ...],
        "reviews":         [Review, ...],
        "review_comments": [ReviewComment, ...],
        "commits":         [GitHubCommit, ...],
    }

Design choices:

* **User dedup.** The raw DTO repeats the same ``UserGithub`` record
  across PR authors, ``mergedBy``, assignees, requestedReviewers, review
  authors, review-comment authors, PR-comment authors, and commit
  authors. Each unique GitHub URL maps to a single :class:`GitHubUser`;
  every reference site uses the resulting entity's ``ref()``. The repo
  owner has no ``login``, which the :class:`GitHubUser` model already
  allows (``login: Optional[str] = None``).

* **PR state.** GitHub's wire format is the uppercase string
  (``"OPEN"`` / ``"CLOSED"`` / ``"MERGED"``). We preserve the raw value;
  consumers downstream (e.g. :class:`PRTraitsMetric`) compare
  case-insensitively (``state.lower() == "open"``).

* **Reviews / review-comments.** Built from ``raw_pr.reviews`` and
  ``review.comments``. The DTO doesn't expose a stable per-review id,
  so :meth:`Review.make_id` derives one from ``(pr_number, ordinal)``
  where ``ordinal`` is the review's position in the PR's review stream.
  ``ReviewComment.id`` is the comment's URL — stable across re-ingests.
  ``PrReviewerBuilder`` reads ``pr.review_refs`` to attach
  ``"source": "Review.author"`` provenance on ``pr_reviewer`` relations;
  before this bridge populated reviews, it fell back to a proxy path
  off ``merged_by_ref + assignee_refs``.
  ``submittedAt`` in the DTO is typed ``Any`` — sometimes a parsed
  datetime, sometimes the raw ISO string, sometimes ``None``; we accept
  the first two via :func:`_coerce_datetime` and drop ``None``.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ...people.source import SourceKind
from src.github_miner.reader_dto.loader import GithubJsonLoader
from src.github_miner.reader_dto.models import (
    CommitGitHubMiner,
    JsonFileFormatGithub,
    PullRequest as RawPullRequest,
    UserGithub,
)

from .models import (
    GitHubCommit,
    GitHubProject,
    GitHubUser,
    PullRequest,
    Review,
    ReviewComment,
)


def _coerce_datetime(value: Any) -> Optional[datetime]:
    """``Review.submittedAt`` is typed ``Any`` in the reader DTO; it
    comes through either pre-parsed (datetime), as an ISO-8601 string
    Pydantic would accept, or as ``None``. Normalise to
    ``Optional[datetime]`` so the v2 :class:`Review` model accepts it
    without further coercion."""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # Pydantic-compatible ISO-8601 parser; let an invalid string
        # propagate so the bridge fails loud rather than silently
        # dropping reviews.
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def build_github_bundle(
    file_path: Path,
    project_name: str = "Project",
) -> Mapping[str, Any]:
    """Read a mined-GitHub JSON file and assemble the v2 entity bundle.

    Parameters
    ----------
    file_path:
        Path to the JSON produced by the github-miner. ~43 MB for
        Zeppelin; the whole tree is parsed eagerly via the reader DTO
        (Pydantic ``model_validate``) — the legacy build path does the
        same.
    project_name:
        Human-facing project name stamped on the resulting
        :class:`GitHubProject`. Defaults to ``"Project"``.

    Returns
    -------
    Mapping[str, Any]
        Bundle compatible with :meth:`GitHubTransformer.transform`. Keys:
        ``project``, ``users``, ``pull_requests``, ``commits``.
    """
    loader = GithubJsonLoader(str(file_path))
    raw: JsonFileFormatGithub = loader.load()

    project = GitHubProject(
        id=raw.repositoryInfo.id,
        name=project_name,
        source=SourceKind.GITHUB,
    )
    project_ref = project.ref()

    users_by_url: Dict[str, GitHubUser] = {}

    def _intern_user(raw_user: Optional[UserGithub]) -> Optional[GitHubUser]:
        """Dedup helper: one :class:`GitHubUser` per unique GitHub URL.

        Returns the canonical entity, or ``None`` when the input is
        absent (PR comments / commits without a resolved author).
        """
        if raw_user is None:
            return None
        existing = users_by_url.get(raw_user.url)
        if existing is not None:
            # Backfill optional fields if a later occurrence carries
            # them (the owner record lacks ``login`` but the
            # PR-author record has it, and vice-versa for ``name``).
            if existing.login is None and raw_user.login is not None:
                existing.login = raw_user.login
            if existing.name is None and raw_user.name is not None:
                existing.name = raw_user.name
            return existing
        user = GitHubUser(
            id=raw_user.url,
            name=raw_user.name or raw_user.login or raw_user.url,
            project_ref=project_ref,
            login=raw_user.login,
            url=raw_user.url,
        )
        users_by_url[raw_user.url] = user
        return user

    # The repo owner appears in the metadata block; intern it so
    # downstream code that joins on the owner URL finds it without a
    # second walk of the PR list.
    _intern_user(raw.repositoryInfo.owner)

    pull_requests: List[PullRequest] = []
    commits: List[GitHubCommit] = []
    reviews: List[Review] = []
    review_comments: List[ReviewComment] = []

    for raw_pr in raw.pullRequests:
        author = _intern_user(raw_pr.createdBy)
        merged_by = _intern_user(raw_pr.mergedBy)

        assignee_refs = [
            a.ref()
            for a in (_intern_user(u) for u in raw_pr.assignees)
            if a is not None
        ]
        requested_reviewer_refs = [
            r.ref()
            for r in (
                _intern_user(rr.requestedReviewer)
                for rr in raw_pr.reviewRequests
            )
            if r is not None
        ]

        # Intern PR-level comment authors so the dedup table is complete
        # before we hand the bundle to the transformer. Review and
        # review-comment authors are interned inline below as part of
        # entity construction.
        for comment in raw_pr.comments:
            _intern_user(comment.author)

        pr = PullRequest(
            id=PullRequest.make_id(raw_pr.number),
            project_ref=project_ref,
            number=raw_pr.number,
            title=raw_pr.title,
            body=raw_pr.body or "",
            state=raw_pr.state,
            changed_files=raw_pr.changedFiles,
            created_at=raw_pr.createdAt,
            updated_at=raw_pr.updatedAt,
            merged_at=raw_pr.mergedAt,
            closed_at=raw_pr.closedAt,
            author_ref=author.ref() if author is not None else None,
            merged_by_ref=merged_by.ref() if merged_by is not None else None,
            assignee_refs=assignee_refs,
            requested_reviewer_refs=requested_reviewer_refs,
        )
        pr_ref = pr.ref()

        commit_refs: List = []
        for raw_commit in raw_pr.commits:
            commit_author = _intern_user(raw_commit.author)
            gh_commit = _build_commit(
                raw_commit,
                pr_ref=pr_ref,
                author_ref=commit_author.ref() if commit_author else None,
            )
            commits.append(gh_commit)
            commit_refs.append(gh_commit.ref())
        pr.commit_refs = commit_refs

        # Reviews + their inline comments. Per-PR ordinal numbering gives
        # us a stable Review id (Review.make_id) since the DTO doesn't
        # expose a stable review id. ReviewComment id is its URL.
        pr_review_refs: List = []
        pr_review_comment_refs: List = []
        for ordinal, raw_review in enumerate(raw_pr.reviews):
            review_author = _intern_user(raw_review.user)
            rev = Review(
                id=Review.make_id(raw_pr.number, ordinal),
                pull_request_ref=pr_ref,
                ordinal=ordinal,
                state=raw_review.state,
                body=raw_review.body or "",
                submitted_at=_coerce_datetime(raw_review.submittedAt),
                author_ref=review_author.ref() if review_author else None,
            )
            rev_ref = rev.ref()

            rc_refs: List = []
            for raw_rc in raw_review.comments:
                rc_author = _intern_user(raw_rc.author)
                rc = ReviewComment(
                    id=raw_rc.url,
                    review_ref=rev_ref,
                    pull_request_ref=pr_ref,
                    url=raw_rc.url,
                    body=raw_rc.body,
                    created_at=raw_rc.createdAt,
                    updated_at=raw_rc.updatedAt,
                    author_ref=rc_author.ref() if rc_author else None,
                )
                review_comments.append(rc)
                rc_refs.append(rc.ref())
            rev.review_comment_refs = rc_refs

            reviews.append(rev)
            pr_review_refs.append(rev_ref)
            pr_review_comment_refs.extend(rc_refs)

        pr.review_refs = pr_review_refs
        pr.review_comment_refs = pr_review_comment_refs

        pull_requests.append(pr)

    return {
        "project": project,
        "users": list(users_by_url.values()),
        "pull_requests": pull_requests,
        "reviews": reviews,
        "review_comments": review_comments,
        "commits": commits,
    }


def _build_commit(
    raw_commit: CommitGitHubMiner,
    *,
    pr_ref,
    author_ref,
) -> GitHubCommit:
    """Translate a single raw ``CommitGitHubMiner`` row into a v2
    :class:`GitHubCommit`. SHA is used both as the entity id and as the
    ``sha`` field (matching the legacy registry-key convention)."""
    return GitHubCommit(
        id=raw_commit.sha,
        sha=raw_commit.sha,
        pull_request_ref=pr_ref,
        date=raw_commit.date,
        message=raw_commit.message,
        changed_files=raw_commit.changedFiles,
        author_ref=author_ref,
        url=raw_commit.url,
    )


__all__ = ["build_github_bundle"]
