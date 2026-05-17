"""Tests for the Chunk-18 :class:`IntentImpactTableBuilder`.

Covers column shape, per-issue-type bucketing, linkage counts via
the ``issue_file`` relation, and the empty-graph fallback.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.common.domains.jira.models import Issue, IssueStatus, IssueType, JiraProject
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind
from src.enrichment.config import EnrichmentConfig
from src.enrichment.overviews import OVERVIEWS
from src.enrichment.overviews.implementations.intent_impact_table import (
    COLUMNS as INT_COLUMNS,
    IntentImpactTableBuilder,
)
from src.enrichment.pipeline import run_pipeline
from src.enrichment.relations import Relation, WindowKind

from tests.enrichment.conftest import (
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)


UTC = timezone.utc


def _seed_intent_graph(name: str):
    """Build a graph with two issues (Bug + Story), each linked to one
    file via an ``issue_file`` relation; commits drive the churn rollup."""
    now = datetime.now(UTC)
    graph, git_project = build_v2_graph(name)
    jira_project = JiraProject(
        id=f"jp:{name}", name=name, source=SourceKind.JIRA,
    )
    graph.add_project(jira_project)

    bug_status = IssueStatus(
        id=f"is:{name}:open",
        project_ref=jira_project.ref(),
        name="Open",
        category="todo",
    )
    graph.issue_statuses.add(bug_status)

    bug_type = IssueType(
        id=f"it:{name}:Bug", project_ref=jira_project.ref(), name="Bug",
    )
    story_type = IssueType(
        id=f"it:{name}:Story", project_ref=jira_project.ref(), name="Story",
    )
    graph.issue_types.add(bug_type)
    graph.issue_types.add(story_type)

    bug = Issue(
        id=f"{name}-1",
        project_ref=jira_project.ref(),
        key=f"{name}-1",
        summary="bug",
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=5),
        status_ref=bug_status.ref(),
        type_ref=bug_type.ref(),
    )
    story = Issue(
        id=f"{name}-2",
        project_ref=jira_project.ref(),
        key=f"{name}-2",
        summary="story",
        created_at=now - timedelta(days=20),
        updated_at=now - timedelta(days=15),
        status_ref=bug_status.ref(),
        type_ref=story_type.ref(),
    )
    graph.issues.add(bug)
    graph.issues.add(story)

    # Git side: two commits touching two files, referenced from the
    # two issues via ``issue_file`` relations.
    alice = make_account("Alice", "a@x", git_project.ref())
    graph.git_accounts.add(alice)
    bug_file = make_file("src/bug.py", git_project.ref())
    story_file = make_file("src/story.py", git_project.ref())
    graph.files.add(bug_file)
    graph.files.add(story_file)

    c_bug = make_commit(
        f"sha-{name}-bug", "fix bug", alice,
        now - timedelta(days=5), git_project.ref(),
    )
    c_story = make_commit(
        f"sha-{name}-story", "add story", alice,
        now - timedelta(days=15), git_project.ref(),
    )
    graph.commits.add(c_bug)
    graph.commits.add(c_story)
    add_change(graph, c_bug, bug_file, added=12, deleted=3)
    add_change(graph, c_story, story_file, added=40, deleted=0)

    # Add the issue_file relations directly so the overview doesn't
    # depend on the regex match (the bug message above doesn't contain
    # the issue key explicitly).
    for issue, file_ in ((bug, bug_file), (story, story_file)):
        rid = Relation.canonical_id(
            issue.ref(), file_.ref(), "issue_file", WindowKind.LIFETIME,
        )
        graph.relations.add(Relation(
            id=rid,
            source=issue.ref(),
            target=file_.ref(),
            relation_kind="issue_file",
            window=WindowKind.LIFETIME,
            strength=1.0,
        ))

    return graph, git_project, now


def test_intent_impact_registered_with_expected_columns():
    assert "intent_impact" in OVERVIEWS.names()
    assert OVERVIEWS.get("intent_impact") is IntentImpactTableBuilder
    graph, _ = build_v2_graph("ii-cols")
    table = IntentImpactTableBuilder().build(graph, EnrichmentConfig())
    assert table.columns == INT_COLUMNS
    assert table.entity_kind == "issue"


def test_intent_impact_empty_graph_emits_only_project_row():
    graph, _ = build_v2_graph("ii-empty")
    table = IntentImpactTableBuilder().build(graph, EnrichmentConfig())
    assert [r.entity_id for r in table.rows] == ["(project)"]
    proj = table.rows[0]
    assert proj.cells["issue_count"].lifetime_value == 0


def test_intent_impact_rows_split_by_issue_type():
    graph, _, _ = _seed_intent_graph("ii-rows")
    run_pipeline(graph, EnrichmentConfig())
    table = IntentImpactTableBuilder().build(graph, EnrichmentConfig())
    row_ids = {r.entity_id for r in table.rows}
    assert "(project)" in row_ids
    assert "Bug" in row_ids
    assert "Story" in row_ids


def test_intent_impact_project_row_aggregates_linked_activity():
    graph, _, _ = _seed_intent_graph("ii-totals")
    run_pipeline(graph, EnrichmentConfig())
    table = IntentImpactTableBuilder().build(graph, EnrichmentConfig())
    project = next(r for r in table.rows if r.entity_id == "(project)")
    assert project.cells["issue_count"].lifetime_value == 2
    # Two commits linked through the two issue_file relations.
    assert project.cells["linked_commits"].lifetime_value == 2
    # 12+3 + 40 = 55 lines of churn across both commits.
    assert project.cells["linked_churn"].lifetime_value == 55
    # 2 distinct files touched by the linked commits.
    assert project.cells["linked_files"].lifetime_value == 2
    # Avg churn per issue: 55 / 2 = 27.5.
    assert project.cells["avg_churn_per_issue"].lifetime_value == 27.5


def test_intent_impact_per_type_row_isolates_its_bucket():
    graph, _, _ = _seed_intent_graph("ii-bucket")
    run_pipeline(graph, EnrichmentConfig())
    table = IntentImpactTableBuilder().build(graph, EnrichmentConfig())
    by_id = {r.entity_id: r for r in table.rows}
    bug_row = by_id["Bug"]
    assert bug_row.cells["issue_count"].lifetime_value == 1
    assert bug_row.cells["linked_commits"].lifetime_value == 1
    # Bug fixture: 12 + 3 = 15 lines of churn on the one linked commit.
    assert bug_row.cells["linked_churn"].lifetime_value == 15
    assert bug_row.cells["linked_files"].lifetime_value == 1


def test_intent_impact_returns_table_when_issues_registry_missing(monkeypatch):
    """When :class:`Graph.issues` is wholly unavailable the builder still
    returns an empty-rows :class:`OverviewTable` (no exception)."""
    graph, _ = build_v2_graph("ii-no-issues")
    # Simulate an absent registry — overwrite Pydantic field.
    graph.__dict__["issues"] = None
    table = IntentImpactTableBuilder().build(graph, EnrichmentConfig())
    assert table.rows == []
    assert table.columns == INT_COLUMNS
