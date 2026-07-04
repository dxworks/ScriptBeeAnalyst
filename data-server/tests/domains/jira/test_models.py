"""Jira-domain entity construction tests.

Covers:

* every entity class instantiates with valid fields,
* cross-entity references are :class:`EntityRef` (never Python refs),
* ``extra="forbid"`` rejects unknown attributes (inherited from Entity),
* class attributes propagate (``kind``, ``source``),
* value objects (``IssueTransition``, ``TransitionItem``, ``Comment``) are
  frozen and equality-by-value.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.common.domains.jira import (
    Comment,
    Issue,
    IssueStatus,
    IssueTransition,
    IssueType,
    JiraProject,
    JiraUser,
    TransitionItem,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "jira-proj-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)


# ---------------------------------------------------------------------------
# JiraProject
# ---------------------------------------------------------------------------


def test_jira_project_construct_and_metadata():
    proj = JiraProject(id=PROJECT_ID, name="ZEPPELIN", source=SourceKind.JIRA)
    assert proj.id == PROJECT_ID
    assert proj.kind == EntityKind.PROJECT
    assert proj.source == SourceKind.JIRA
    assert proj.name == "ZEPPELIN"
    assert proj.linked_project_ids == []
    assert proj.ref() == EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)


def test_jira_project_transformer_class_returns_jira_transformer():
    from src.common.domains.jira.transformer import JiraTransformer

    proj = JiraProject(id=PROJECT_ID, name="ZEPPELIN", source=SourceKind.JIRA)
    assert proj.transformer_class() is JiraTransformer


def test_jira_project_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        JiraProject(
            id=PROJECT_ID,
            name="z",
            source=SourceKind.JIRA,
            mystery=1,  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# JiraUser
# ---------------------------------------------------------------------------


def test_jira_user_construct_with_class_attr_source():
    u = JiraUser(
        id="https://jira.example.com/users/alice",
        name="Alice",
        project_ref=PROJECT_REF,
        key="alice",
        link="https://jira.example.com/users/alice",
    )
    assert u.kind == EntityKind.JIRA_USER
    assert u.source == SourceKind.JIRA
    assert u.id == "https://jira.example.com/users/alice"
    assert u.key == "alice"
    assert u.link == "https://jira.example.com/users/alice"
    assert u.unified_user_id is None
    assert u.ref() == EntityRef(
        kind=EntityKind.JIRA_USER, id="https://jira.example.com/users/alice"
    )


def test_jira_user_link_is_optional():
    u = JiraUser(id="u1", name="X", project_ref=PROJECT_REF, key="x")
    assert u.link is None


def test_jira_user_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        JiraUser(
            id="u1",
            name="X",
            project_ref=PROJECT_REF,
            key="x",
            issues_as_assignee=[],  # legacy field that v2 dropped
        )


def test_jira_user_unified_user_id_round_trip():
    u = JiraUser(
        id="u1",
        name="X",
        project_ref=PROJECT_REF,
        key="x",
        unified_user_id="uu-1",
    )
    payload = u.model_dump()
    restored = JiraUser.model_validate(payload)
    assert restored.unified_user_id == "uu-1"
    assert restored.source == SourceKind.JIRA


# ---------------------------------------------------------------------------
# IssueStatus / IssueType
# ---------------------------------------------------------------------------


def test_issue_status_construct_and_extra_forbidden():
    s = IssueStatus(
        id="10001",
        project_ref=PROJECT_REF,
        name="In Progress",
        category="indeterminate",
    )
    assert s.kind == EntityKind.ISSUE_STATUS
    assert s.ref() == EntityRef(kind=EntityKind.ISSUE_STATUS, id="10001")
    with pytest.raises(ValidationError):
        IssueStatus(
            id="x",
            project_ref=PROJECT_REF,
            name="x",
            category="new",
            statusCategory={"key": "new"},  # legacy nested field — dropped
        )


def test_issue_type_construct_and_defaults():
    t = IssueType(
        id="3", project_ref=PROJECT_REF, name="Task", is_sub_task=False
    )
    assert t.kind == EntityKind.ISSUE_TYPE
    assert t.description == ""
    assert t.is_sub_task is False
    # Round-trip via model_dump preserves the optional fields.
    restored = IssueType.model_validate(t.model_dump())
    assert restored == t


# ---------------------------------------------------------------------------
# Issue
# ---------------------------------------------------------------------------


def _alice_ref() -> EntityRef:
    return EntityRef(kind=EntityKind.JIRA_USER, id="alice")


def _bob_ref() -> EntityRef:
    return EntityRef(kind=EntityKind.JIRA_USER, id="bob")


def _status_ref(sid: str = "10000") -> EntityRef:
    return EntityRef(kind=EntityKind.ISSUE_STATUS, id=sid)


def _type_ref(tid: str = "1") -> EntityRef:
    return EntityRef(kind=EntityKind.ISSUE_TYPE, id=tid)


def test_issue_construct_with_entity_refs():
    issue = Issue(
        id="PROJ-1",
        project_ref=PROJECT_REF,
        key="PROJ-1",
        summary="fix bug",
        description="long desc",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        numeric_id=12345,
        status_ref=_status_ref(),
        type_ref=_type_ref(),
        creator_ref=_alice_ref(),
        reporter_ref=_alice_ref(),
        assignee_refs=[_alice_ref(), _bob_ref()],
        priority="High",
    )
    assert issue.kind == EntityKind.ISSUE
    assert issue.id == "PROJ-1"
    assert issue.key == "PROJ-1"
    assert issue.assignee_refs == [_alice_ref(), _bob_ref()]
    assert issue.parent_ref is None
    assert issue.transitions == []


def test_issue_rejects_legacy_python_object_assignees():
    """Cross-entity refs must be EntityRef — never a Python object."""
    user = JiraUser(id="alice", name="Alice", project_ref=PROJECT_REF, key="a")
    with pytest.raises(ValidationError):
        Issue(
            id="PROJ-1",
            project_ref=PROJECT_REF,
            key="PROJ-1",
            summary="s",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            status_ref=_status_ref(),
            type_ref=_type_ref(),
            assignee_refs=[user],  # type: ignore[list-item]
        )


def test_issue_extra_fields_forbidden():
    """Legacy ``children`` / ``git_commits`` / ``pull_requests`` are dropped."""
    with pytest.raises(ValidationError):
        Issue(
            id="x",
            project_ref=PROJECT_REF,
            key="x",
            summary="s",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            status_ref=_status_ref(),
            type_ref=_type_ref(),
            children=[],  # legacy field
        )
    with pytest.raises(ValidationError):
        Issue(
            id="x",
            project_ref=PROJECT_REF,
            key="x",
            summary="s",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            status_ref=_status_ref(),
            type_ref=_type_ref(),
            git_commits=[],  # legacy cross-source link
        )


def test_issue_with_transitions_and_comments():
    issue = Issue(
        id="PROJ-2",
        project_ref=PROJECT_REF,
        key="PROJ-2",
        summary="s",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        status_ref=_status_ref(),
        type_ref=_type_ref(),
        transitions=[
            IssueTransition(
                id=1,
                created=datetime(2024, 1, 1, 12, tzinfo=timezone.utc),
                changed_fields=["status"],
                items=[
                    TransitionItem(
                        field="status",
                        from_string="Open",
                        to_string="In Progress",
                    )
                ],
                user_ref=_alice_ref(),
            )
        ],
        comments=[
            Comment(
                body="LGTM",
                created=datetime(2024, 1, 1, 13, tzinfo=timezone.utc),
                updated=datetime(2024, 1, 1, 13, tzinfo=timezone.utc),
                author_ref=_alice_ref(),
            )
        ],
    )
    assert len(issue.transitions) == 1
    assert issue.transitions[0].items[0].to_string == "In Progress"
    assert len(issue.comments) == 1
    assert issue.comments[0].body == "LGTM"


# ---------------------------------------------------------------------------
# Value object semantics
# ---------------------------------------------------------------------------


def test_transition_item_is_frozen_value_object():
    a = TransitionItem(field="status", from_string="Open", to_string="Done")
    b = TransitionItem(field="status", from_string="Open", to_string="Done")
    assert a == b
    with pytest.raises(ValidationError):
        a.field = "priority"  # type: ignore[misc]


def test_issue_transition_is_frozen_value_object():
    t = IssueTransition(
        id=1,
        created=datetime(2024, 1, 1, tzinfo=timezone.utc),
        user_ref=_alice_ref(),
    )
    with pytest.raises(ValidationError):
        t.id = 2  # type: ignore[misc]


def test_comment_is_frozen_value_object():
    c = Comment(
        body="hi",
        created=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(ValidationError):
        c.body = "bye"  # type: ignore[misc]
