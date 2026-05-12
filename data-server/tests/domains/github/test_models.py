"""GitHub-domain entity construction tests.

Covers:

* every entity class instantiates with valid fields,
* cross-entity references are :class:`EntityRef` (never Python refs),
* ``extra="forbid"`` rejects unknown attributes (inherited from Entity),
* class attributes propagate (``kind``, ``source``),
* derived helpers (``PullRequest.make_id``, ``Review.make_id``) work.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.common.domains.github import (
    GitHubCommit,
    GitHubProject,
    GitHubUser,
    PullRequest,
    Review,
    ReviewComment,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "github-proj-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)


# ---------------------------------------------------------------------------
# GitHubProject
# ---------------------------------------------------------------------------


def test_github_project_construct_and_metadata():
    proj = GitHubProject(
        id=PROJECT_ID, name="zeppelin/zeppelin", source=SourceKind.GITHUB
    )
    assert proj.id == PROJECT_ID
    assert proj.kind == EntityKind.PROJECT
    assert proj.source == SourceKind.GITHUB
    assert proj.name == "zeppelin/zeppelin"
    assert proj.linked_project_ids == []
    assert proj.ref() == EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)


def test_github_project_transformer_class_returns_github_transformer():
    from src.common.domains.github.transformer import GitHubTransformer

    proj = GitHubProject(id=PROJECT_ID, name="x", source=SourceKind.GITHUB)
    assert proj.transformer_class() is GitHubTransformer


def test_github_project_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        GitHubProject(
            id=PROJECT_ID,
            name="x",
            source=SourceKind.GITHUB,
            mystery=1,  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# GitHubUser
# ---------------------------------------------------------------------------


def test_github_user_construct_with_class_attr_source():
    u = GitHubUser(
        id="https://github.com/alice",
        name="Alice",
        project_ref=PROJECT_REF,
        login="alice",
        url="https://github.com/alice",
    )
    assert u.kind == EntityKind.GITHUB_USER
    assert u.source == SourceKind.GITHUB
    assert u.id == "https://github.com/alice"
    assert u.login == "alice"
    assert u.url == "https://github.com/alice"
    assert u.unified_user_id is None


def test_github_user_login_is_optional():
    """Legacy DTO docs note repo-owner records sometimes lack a login."""
    u = GitHubUser(
        id="https://github.com/owner",
        name="Owner",
        project_ref=PROJECT_REF,
        url="https://github.com/owner",
    )
    assert u.login is None


def test_github_user_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        GitHubUser(
            id="u1",
            name="X",
            project_ref=PROJECT_REF,
            pull_requests_as_creator=[],  # legacy field dropped
        )


def test_github_user_unified_user_id_round_trip():
    u = GitHubUser(
        id="u1",
        name="X",
        project_ref=PROJECT_REF,
        login="x",
        unified_user_id="uu-1",
    )
    payload = u.model_dump()
    restored = GitHubUser.model_validate(payload)
    assert restored.unified_user_id == "uu-1"
    assert restored.source == SourceKind.GITHUB


# ---------------------------------------------------------------------------
# PullRequest
# ---------------------------------------------------------------------------


def _alice_ref() -> EntityRef:
    return EntityRef(kind=EntityKind.GITHUB_USER, id="alice")


def _bob_ref() -> EntityRef:
    return EntityRef(kind=EntityKind.GITHUB_USER, id="bob")


def test_pull_request_construct_and_make_id():
    assert PullRequest.make_id(123) == "123"
    pr = PullRequest(
        id=PullRequest.make_id(7),
        project_ref=PROJECT_REF,
        number=7,
        title="add feature",
        body="long body",
        state="open",
        changed_files=4,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        author_ref=_alice_ref(),
        assignee_refs=[_bob_ref()],
    )
    assert pr.kind == EntityKind.PULL_REQUEST
    assert pr.id == "7"
    assert pr.number == 7
    assert pr.merged_by_ref is None
    assert pr.commit_refs == []
    assert pr.review_refs == []
    assert pr.review_comment_refs == []


def test_pull_request_rejects_python_assignee_object():
    user = GitHubUser(id="alice", name="Alice", project_ref=PROJECT_REF, login="a")
    with pytest.raises(ValidationError):
        PullRequest(
            id="1",
            project_ref=PROJECT_REF,
            number=1,
            title="t",
            state="open",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            assignee_refs=[user],  # type: ignore[list-item]
        )


def test_pull_request_extra_fields_forbidden():
    """Legacy ``issues`` / ``git_commits`` / ``git_hub_commits`` are dropped."""
    with pytest.raises(ValidationError):
        PullRequest(
            id="1",
            project_ref=PROJECT_REF,
            number=1,
            title="t",
            state="open",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            issues=[],  # legacy cross-source link
        )
    with pytest.raises(ValidationError):
        PullRequest(
            id="1",
            project_ref=PROJECT_REF,
            number=1,
            title="t",
            state="open",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            git_hub_commits=[],  # legacy field renamed to commit_refs
        )


# ---------------------------------------------------------------------------
# Review + ReviewComment
# ---------------------------------------------------------------------------


def _pr_ref(number: int = 7) -> EntityRef:
    return EntityRef(kind=EntityKind.PULL_REQUEST, id=str(number))


def test_review_make_id_and_construct():
    rid = Review.make_id(7, 0)
    assert rid == "7#0"
    r = Review(
        id=rid,
        pull_request_ref=_pr_ref(7),
        ordinal=0,
        state="APPROVED",
        body="LGTM",
        submitted_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_ref=_alice_ref(),
    )
    assert r.kind == EntityKind.REVIEW
    assert r.id == "7#0"
    assert r.review_comment_refs == []


def test_review_comment_construct_and_optional_fields():
    rc = ReviewComment(
        id="https://github.com/repo/pull/7#discussion_r1",
        review_ref=EntityRef(kind=EntityKind.REVIEW, id="7#0"),
        pull_request_ref=_pr_ref(7),
        url="https://github.com/repo/pull/7#discussion_r1",
        body="please rename",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_ref=_alice_ref(),
        file_path="src/app.py",
        line=42,
    )
    assert rc.kind == EntityKind.REVIEW_COMMENT
    assert rc.file_path == "src/app.py"
    assert rc.line == 42
    # Optional fields default to None.
    rc2 = ReviewComment(
        id="x",
        review_ref=EntityRef(kind=EntityKind.REVIEW, id="7#0"),
        pull_request_ref=_pr_ref(7),
        url="x",
        body="b",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    assert rc2.file_path is None
    assert rc2.line is None
    assert rc2.author_ref is None


# ---------------------------------------------------------------------------
# GitHubCommit
# ---------------------------------------------------------------------------


def test_github_commit_construct():
    c = GitHubCommit(
        id="abc123",
        pull_request_ref=_pr_ref(7),
        sha="abc123",
        date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        message="fix",
        changed_files=2,
        author_ref=_alice_ref(),
        url="https://github.com/repo/commit/abc123",
    )
    assert c.kind == EntityKind.GITHUB_COMMIT
    assert c.id == "abc123"
    assert c.sha == "abc123"
    assert c.changed_files == 2


def test_github_commit_extra_fields_forbidden():
    """Legacy ``pull_requests: List[PullRequest]`` back-pointer is dropped."""
    with pytest.raises(ValidationError):
        GitHubCommit(
            id="x",
            pull_request_ref=_pr_ref(),
            sha="x",
            date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            message="m",
            pull_requests=[],  # legacy back-pointer
        )
