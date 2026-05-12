"""Jira-domain Transformer tests.

Parallel to ``tests/domains/git/test_transformer.py``. The Chunk-5
transformer also accepts pre-built entity bundles and delegates validation
to the shared :meth:`Transformer.collect_bundle` helper.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.common.domains import Transformer, TransformResult
from src.common.domains.jira import (
    Issue,
    IssueStatus,
    IssueType,
    JiraProject,
    JiraTransformer,
    JiraUser,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "jira-1"


def _build_entity_bundle() -> dict:
    project = JiraProject(id=PROJECT_ID, name="ZEPPELIN", source=SourceKind.JIRA)
    project_ref = project.ref()
    alice = JiraUser(
        id="u/alice",
        name="Alice",
        project_ref=project_ref,
        key="alice",
        link="u/alice",
    )
    open_s = IssueStatus(
        id="1", project_ref=project_ref, name="Open", category="new"
    )
    bug = IssueType(id="1", project_ref=project_ref, name="Bug")
    issue = Issue(
        id="PROJ-1",
        project_ref=project_ref,
        key="PROJ-1",
        summary="s",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        status_ref=open_s.ref(),
        type_ref=bug.ref(),
        assignee_refs=[alice.ref()],
    )
    return {
        "project": project,
        "users": [alice],
        "issues": [issue],
        "issue_statuses": [open_s],
        "issue_types": [bug],
    }


def test_jira_transformer_is_a_transformer():
    assert issubclass(JiraTransformer, Transformer)
    assert JiraTransformer.source == SourceKind.JIRA


def test_jira_transformer_happy_path():
    bundle = _build_entity_bundle()
    result = JiraTransformer().transform(bundle)
    assert isinstance(result, TransformResult)
    assert result.project is bundle["project"]
    assert set(result.entities) == {
        EntityKind.JIRA_USER,
        EntityKind.ISSUE,
        EntityKind.ISSUE_STATUS,
        EntityKind.ISSUE_TYPE,
    }
    assert [u.id for u in result.entities[EntityKind.JIRA_USER]] == ["u/alice"]
    assert [i.id for i in result.entities[EntityKind.ISSUE]] == ["PROJ-1"]
    assert len(result.entities[EntityKind.ISSUE_STATUS]) == 1
    assert len(result.entities[EntityKind.ISSUE_TYPE]) == 1


def test_jira_transformer_handles_missing_optional_buckets():
    bundle = _build_entity_bundle()
    del bundle["issues"]
    del bundle["users"]
    result = JiraTransformer().transform(bundle)
    assert result.entities[EntityKind.JIRA_USER] == []
    assert result.entities[EntityKind.ISSUE] == []
    # Buckets the bundle still carried are unaffected.
    assert len(result.entities[EntityKind.ISSUE_STATUS]) == 1


def test_jira_transformer_rejects_missing_project_key():
    with pytest.raises(ValueError, match="'project'"):
        JiraTransformer().transform({"issues": []})


def test_jira_transformer_rejects_wrong_project_type():
    with pytest.raises(TypeError, match="JiraProject"):
        JiraTransformer().transform({"project": "not-a-project"})


def test_jira_transformer_rejects_wrong_entity_in_bucket():
    bundle = _build_entity_bundle()
    # IssueType in the issues bucket — must be Issue.
    bundle["issues"] = [bundle["issue_types"][0]]
    with pytest.raises(TypeError, match="issues"):
        JiraTransformer().transform(bundle)


def test_jira_transformer_rejects_unknown_bundle_keys():
    bundle = _build_entity_bundle()
    bundle["commits"] = []  # git bucket leaked into a jira bundle
    with pytest.raises(ValueError, match="unknown bundle keys"):
        JiraTransformer().transform(bundle)


def test_jira_transformer_rejects_raw_dto_for_now():
    """Raw ``JsonFileFormatJira`` ingestion is deferred to Chunk 8 —
    same boundary as ``GitTransformer``."""
    with pytest.raises(NotImplementedError, match="entity-bundle"):
        JiraTransformer().transform(object())
