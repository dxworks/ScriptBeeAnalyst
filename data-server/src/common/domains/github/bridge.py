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

* **Reviews / review-comments.** The transformer's ``_BUCKET_SPECS``
  lists ``reviews`` and ``review_comments`` as accepted bundle keys, but
  the v2 bundle shape the dispatcher contract requires (and the
  ``_github_bundle()`` test fixture) is the four-key form above.
  Reviews and review-comments are not produced by this bridge today —
  the legacy reader DTO doesn't carry a stable ``review_id``, and the
  v2 :class:`Review` model uses an ordinal-based composite id that the
  Phase-2 GitHubTransformer.transform DTO path will own. Keeping the
  bundle to the four canonical keys keeps the bridge faithful to the
  test fixture and the legacy build path (which also dropped reviews
  at the entity level).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ...people import SourceKind
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
)


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

        # Walk every nested author so the dedup table is complete
        # *before* we hand the bundle to the transformer.
        for comment in raw_pr.comments:
            _intern_user(comment.author)
        for review in raw_pr.reviews:
            _intern_user(review.user)
            for review_comment in review.comments:
                _intern_user(review_comment.author)

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

        pull_requests.append(pr)

    return {
        "project": project,
        "users": list(users_by_url.values()),
        "pull_requests": pull_requests,
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
