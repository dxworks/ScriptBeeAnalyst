"""Jira-domain registry tests.

Covers every registry's CRUD + declared indexes + pickle round-trip.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.common.domains.jira import (
    Issue,
    IssueRegistry,
    IssueStatus,
    IssueStatusRegistry,
    IssueType,
    IssueTypeRegistry,
    JiraProject,
    JiraProjectRegistry,
    JiraUser,
    JiraUserRegistry,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind
from src.common.pickle_store import PickleStore


PROJECT_ID = "jira-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _user(
    name: str, key: str, link: str | None = None, uu: str | None = None
) -> JiraUser:
    return JiraUser(
        id=link or key,
        name=name,
        project_ref=PROJECT_REF,
        key=key,
        link=link,
        unified_user_id=uu,
    )


def _status(sid: str, name: str, category: str = "new") -> IssueStatus:
    return IssueStatus(
        id=sid, project_ref=PROJECT_REF, name=name, category=category
    )


def _type(tid: str, name: str) -> IssueType:
    return IssueType(id=tid, project_ref=PROJECT_REF, name=name)


def _issue(
    key: str,
    status_ref: EntityRef,
    type_ref: EntityRef,
    *,
    assignee_refs: list[EntityRef] | None = None,
    reporter_ref: EntityRef | None = None,
    creator_ref: EntityRef | None = None,
    parent_key: str | None = None,
    project_ref: EntityRef = PROJECT_REF,
) -> Issue:
    return Issue(
        id=key,
        project_ref=project_ref,
        key=key,
        summary=f"summary of {key}",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        status_ref=status_ref,
        type_ref=type_ref,
        assignee_refs=assignee_refs or [],
        reporter_ref=reporter_ref,
        creator_ref=creator_ref,
        parent_ref=(
            EntityRef(kind=EntityKind.ISSUE, id=parent_key)
            if parent_key
            else None
        ),
    )


# ---------------------------------------------------------------------------
# JiraProjectRegistry
# ---------------------------------------------------------------------------


def test_jira_project_registry_add_and_by_name():
    reg = JiraProjectRegistry()
    p1 = JiraProject(id="j1", name="ZEPPELIN", source=SourceKind.JIRA)
    p2 = JiraProject(id="j2", name="ZEPPELIN", source=SourceKind.JIRA)
    p3 = JiraProject(id="j3", name="OTHER", source=SourceKind.JIRA)
    reg.add(p1)
    reg.add(p2)
    reg.add(p3)
    assert len(reg) == 3
    assert reg.get("j1") is p1
    assert {p.id for p in reg.by_name["ZEPPELIN"]} == {"j1", "j2"}
    assert {p.id for p in reg.by_name["OTHER"]} == {"j3"}


# ---------------------------------------------------------------------------
# JiraUserRegistry
# ---------------------------------------------------------------------------


def test_jira_user_registry_indexes():
    reg = JiraUserRegistry()
    alice = _user("Alice", key="alice", link="u/alice", uu="uu-1")
    bob = _user("Bob", key="bob", link="u/bob", uu="uu-2")
    other_proj_ref = EntityRef(kind=EntityKind.PROJECT, id="jira-2")
    carol = JiraUser(
        id="u/carol",
        name="Carol",
        project_ref=other_proj_ref,
        key="carol",
        link="u/carol",
        unified_user_id="uu-1",
    )
    reg.add(alice)
    reg.add(bob)
    reg.add(carol)

    assert {u.id for u in reg.by_key["alice"]} == {alice.id}
    assert {u.id for u in reg.by_project[PROJECT_REF]} == {alice.id, bob.id}
    assert {u.id for u in reg.by_project[other_proj_ref]} == {carol.id}
    assert {u.id for u in reg.by_unified_user["uu-1"]} == {alice.id, carol.id}
    assert {u.id for u in reg.by_unified_user["uu-2"]} == {bob.id}
    assert reg.by_unified_user[None] == ()


def test_jira_user_registry_remove_updates_indexes():
    reg = JiraUserRegistry()
    u = _user("X", key="x", link="u/x", uu="uu-1")
    reg.add(u)
    assert reg.by_unified_user["uu-1"] == (u,)
    reg.remove(u.id)
    assert reg.by_unified_user["uu-1"] == ()
    assert reg.by_key["x"] == ()
    assert reg.by_project[PROJECT_REF] == ()


# ---------------------------------------------------------------------------
# IssueStatusRegistry / IssueTypeRegistry
# ---------------------------------------------------------------------------


def test_issue_status_registry_indexes():
    reg = IssueStatusRegistry()
    reg.add(_status("1", "Open", "new"))
    reg.add(_status("2", "In Progress", "indeterminate"))
    reg.add(_status("3", "Done", "done"))
    reg.add(_status("4", "Closed", "done"))

    assert {s.id for s in reg.by_project[PROJECT_REF]} == {"1", "2", "3", "4"}
    assert {s.id for s in reg.by_name["Done"]} == {"3"}
    assert {s.id for s in reg.by_category["done"]} == {"3", "4"}
    assert {s.id for s in reg.by_category["new"]} == {"1"}


def test_issue_type_registry_indexes():
    reg = IssueTypeRegistry()
    reg.add(_type("1", "Bug"))
    reg.add(_type("2", "Story"))
    reg.add(_type("3", "Bug"))

    assert {t.id for t in reg.by_project[PROJECT_REF]} == {"1", "2", "3"}
    assert {t.id for t in reg.by_name["Bug"]} == {"1", "3"}
    assert {t.id for t in reg.by_name["Story"]} == {"2"}


# ---------------------------------------------------------------------------
# IssueRegistry
# ---------------------------------------------------------------------------


def test_issue_registry_indexes():
    reg = IssueRegistry()
    open_s = _status("1", "Open")
    inprog = _status("2", "In Progress", "indeterminate")
    bug = _type("1", "Bug")
    story = _type("2", "Story")
    alice = _user("A", "alice", "u/alice")
    bob = _user("B", "bob", "u/bob")

    parent = _issue(
        "PROJ-1",
        open_s.ref(),
        story.ref(),
        reporter_ref=alice.ref(),
        creator_ref=alice.ref(),
    )
    child1 = _issue(
        "PROJ-2",
        inprog.ref(),
        bug.ref(),
        assignee_refs=[alice.ref()],
        reporter_ref=bob.ref(),
        parent_key="PROJ-1",
    )
    child2 = _issue(
        "PROJ-3",
        open_s.ref(),
        bug.ref(),
        assignee_refs=[alice.ref(), bob.ref()],
        parent_key="PROJ-1",
    )
    reg.add(parent)
    reg.add(child1)
    reg.add(child2)

    # by_project
    assert {i.id for i in reg.by_project[PROJECT_REF]} == {
        "PROJ-1",
        "PROJ-2",
        "PROJ-3",
    }
    # by_status
    assert {i.id for i in reg.by_status[open_s.ref()]} == {"PROJ-1", "PROJ-3"}
    assert {i.id for i in reg.by_status[inprog.ref()]} == {"PROJ-2"}
    # by_type
    assert {i.id for i in reg.by_type[bug.ref()]} == {"PROJ-2", "PROJ-3"}
    assert {i.id for i in reg.by_type[story.ref()]} == {"PROJ-1"}
    # by_assignee fans out
    assert {i.id for i in reg.by_assignee[alice.ref()]} == {"PROJ-2", "PROJ-3"}
    assert {i.id for i in reg.by_assignee[bob.ref()]} == {"PROJ-3"}
    # by_reporter / by_creator with None skipping
    assert {i.id for i in reg.by_reporter[alice.ref()]} == {"PROJ-1"}
    assert {i.id for i in reg.by_reporter[bob.ref()]} == {"PROJ-2"}
    # PROJ-3 has no reporter set → None key skipped:
    assert reg.by_reporter[None] == ()
    assert {i.id for i in reg.by_creator[alice.ref()]} == {"PROJ-1"}
    # by_parent replaces legacy ``Issue.children`` — fans out via Optional ref
    parent_ref = EntityRef(kind=EntityKind.ISSUE, id="PROJ-1")
    assert {i.id for i in reg.by_parent[parent_ref]} == {"PROJ-2", "PROJ-3"}
    assert reg.by_parent[None] == ()
    # by_key (same value as id but indexed for the smart-merge UI)
    assert {i.id for i in reg.by_key["PROJ-1"]} == {"PROJ-1"}


def test_issue_registry_remove_updates_indexes():
    reg = IssueRegistry()
    open_s = _status("1", "Open")
    bug = _type("1", "Bug")
    alice = _user("A", "alice", "u/alice")
    i = _issue(
        "PROJ-1", open_s.ref(), bug.ref(), assignee_refs=[alice.ref()]
    )
    reg.add(i)
    assert reg.by_assignee[alice.ref()] == (i,)
    reg.remove(i.id)
    assert reg.by_assignee[alice.ref()] == ()
    assert reg.by_status[open_s.ref()] == ()
    assert reg.by_project[PROJECT_REF] == ()


# ---------------------------------------------------------------------------
# Pickle round-trip via PickleStore
# ---------------------------------------------------------------------------


def test_issue_registry_pickle_round_trip(tmp_path: Path):
    reg = IssueRegistry()
    open_s = _status("1", "Open")
    bug = _type("1", "Bug")
    alice = _user("A", "alice", "u/alice")
    reg.add(_issue("PROJ-1", open_s.ref(), bug.ref(), reporter_ref=alice.ref()))
    reg.add(
        _issue(
            "PROJ-2",
            open_s.ref(),
            bug.ref(),
            assignee_refs=[alice.ref()],
            parent_key="PROJ-1",
        )
    )
    store = PickleStore(tmp_path)
    store.write_registry(EntityKind.ISSUE.value, reg)
    restored = store.read_registry(EntityKind.ISSUE.value, IssueRegistry)
    assert len(restored) == 2
    assert {i.id for i in restored.by_status[open_s.ref()]} == {
        "PROJ-1",
        "PROJ-2",
    }
    parent_ref = EntityRef(kind=EntityKind.ISSUE, id="PROJ-1")
    assert {i.id for i in restored.by_parent[parent_ref]} == {"PROJ-2"}


def test_jira_user_registry_pickle_round_trip(tmp_path: Path):
    reg = JiraUserRegistry()
    reg.add(_user("A", "alice", "u/alice", uu="uu-1"))
    reg.add(_user("B", "bob", "u/bob"))
    store = PickleStore(tmp_path)
    store.write_registry(EntityKind.JIRA_USER.value, reg)
    restored = store.read_registry(
        EntityKind.JIRA_USER.value, JiraUserRegistry
    )
    assert len(restored) == 2
    assert {u.key for u in restored.by_unified_user["uu-1"]} == {"alice"}
    assert restored.by_unified_user[None] == ()
