"""GitHub-domain Transformer tests.

Parallel to ``tests/domains/git/test_transformer.py`` and
``tests/domains/jira/test_transformer.py``. The Chunk-5 GitHub transformer
also accepts pre-built entity bundles and delegates validation to the
shared :meth:`Transformer.collect_bundle` helper.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.common.domains import Transformer, TransformResult
from src.common.domains.github import (
    GitHubCommit,
    GitHubProject,
    GitHubTransformer,
    GitHubUser,
    PullRequest,
    Review,
    ReviewComment,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "github-1"


def _build_entity_bundle() -> dict:
    project = GitHubProject(
        id=PROJECT_ID, name="zep/zep", source=SourceKind.GITHUB
    )
    project_ref = project.ref()
    alice = GitHubUser(
        id="u/alice",
        name="Alice",
        project_ref=project_ref,
        login="alice",
        url="u/alice",
    )
    pr = PullRequest(
        id=PullRequest.make_id(1),
        project_ref=project_ref,
        number=1,
        title="add feature",
        body="",
        state="open",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_ref=alice.ref(),
    )
    review = Review(
        id=Review.make_id(1, 0),
        pull_request_ref=pr.ref(),
        ordinal=0,
        state="APPROVED",
        body="",
        author_ref=alice.ref(),
    )
    comment = ReviewComment(
        id="u/c1",
        review_ref=review.ref(),
        pull_request_ref=pr.ref(),
        url="u/c1",
        body="please rename",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_ref=alice.ref(),
    )
    commit = GitHubCommit(
        id="sha1",
        pull_request_ref=pr.ref(),
        sha="sha1",
        date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        message="commit sha1",
        author_ref=alice.ref(),
    )
    return {
        "project": project,
        "users": [alice],
        "pull_requests": [pr],
        "reviews": [review],
        "review_comments": [comment],
        "commits": [commit],
    }


def test_github_transformer_is_a_transformer():
    assert issubclass(GitHubTransformer, Transformer)
    assert GitHubTransformer.source == SourceKind.GITHUB


def test_github_transformer_happy_path():
    bundle = _build_entity_bundle()
    result = GitHubTransformer().transform(bundle)
    assert isinstance(result, TransformResult)
    assert result.project is bundle["project"]
    assert set(result.entities) == {
        EntityKind.GITHUB_USER,
        EntityKind.PULL_REQUEST,
        EntityKind.REVIEW,
        EntityKind.REVIEW_COMMENT,
        EntityKind.GITHUB_COMMIT,
    }
    assert [u.id for u in result.entities[EntityKind.GITHUB_USER]] == ["u/alice"]
    assert [p.id for p in result.entities[EntityKind.PULL_REQUEST]] == ["1"]
    assert [r.id for r in result.entities[EntityKind.REVIEW]] == ["1#0"]
    assert [c.id for c in result.entities[EntityKind.REVIEW_COMMENT]] == ["u/c1"]
    assert [c.id for c in result.entities[EntityKind.GITHUB_COMMIT]] == ["sha1"]


def test_github_transformer_handles_missing_optional_buckets():
    bundle = _build_entity_bundle()
    del bundle["reviews"]
    del bundle["review_comments"]
    del bundle["commits"]
    result = GitHubTransformer().transform(bundle)
    assert result.entities[EntityKind.REVIEW] == []
    assert result.entities[EntityKind.REVIEW_COMMENT] == []
    assert result.entities[EntityKind.GITHUB_COMMIT] == []
    assert len(result.entities[EntityKind.PULL_REQUEST]) == 1


def test_github_transformer_rejects_missing_project_key():
    with pytest.raises(ValueError, match="'project'"):
        GitHubTransformer().transform({"pull_requests": []})


def test_github_transformer_rejects_wrong_project_type():
    with pytest.raises(TypeError, match="GitHubProject"):
        GitHubTransformer().transform({"project": "not-a-project"})


def test_github_transformer_rejects_wrong_entity_in_bucket():
    bundle = _build_entity_bundle()
    # Commit in the pull_requests bucket — must be PullRequest.
    bundle["pull_requests"] = [bundle["commits"][0]]
    with pytest.raises(TypeError, match="pull_requests"):
        GitHubTransformer().transform(bundle)


def test_github_transformer_rejects_unknown_bundle_keys():
    bundle = _build_entity_bundle()
    bundle["issues"] = []  # jira bucket leaked into a github bundle
    with pytest.raises(ValueError, match="unknown bundle keys"):
        GitHubTransformer().transform(bundle)


def test_github_transformer_rejects_raw_dto_for_now():
    """Raw ``JsonFileFormatGithub`` ingestion is deferred to Chunk 8 —
    same boundary as the git and jira transformers."""
    with pytest.raises(NotImplementedError, match="entity-bundle"):
        GitHubTransformer().transform(object())
