"""Typed Graph fields — instantiation defaults + ``resolve`` round-trip.

Verifies §1.6 of ``architectural_changes.md`` and the Chunk-8 wiring:

* ``Graph(project_id="x")`` constructs with empty registries on every
  typed field.
* Each typed field is an instance of its concrete :class:`Registry`
  subclass (not ``dict``, not ``Any``).
* Adding entities to a few representative typed registries works AND
  :meth:`Graph.resolve` round-trips through the right field.
* :meth:`Graph.registry_for` returns the same typed instance for kinds
  with exactly one matching field (most kinds); ``None`` for
  ``EntityKind.PROJECT`` (which has 7 matching fields).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.common.domains.code_structure.models import (
    CodeMethod,
    CodeStructureProject,
)
from src.common.domains.code_structure.registries import (
    CodeMethodRegistry,
    CodeStructureProjectRegistry,
    CodeTypeRegistry,
)
from src.common.domains.duplication.registries import (
    DuplicationPairRegistry,
    DuplicationProjectRegistry,
)
from src.common.domains.git.models import (
    Change,
    ChangeType,
    Commit,
    File,
    GitAccount,
    GitProject,
)
from src.common.domains.git.registries import (
    ChangeRegistry,
    CommitRegistry,
    FileRegistry,
    GitAccountRegistry,
    GitProjectRegistry,
    HunkRegistry,
)
from src.common.domains.github.models import GitHubProject
from src.common.domains.github.registries import (
    GitHubCommitRegistry,
    GitHubProjectRegistry,
    GitHubUserRegistry,
    PullRequestRegistry,
    ReviewCommentRegistry,
    ReviewRegistry,
)
from src.common.domains.jira.models import Issue, IssueStatus, JiraProject, JiraUser
from src.common.domains.jira.registries import (
    IssueRegistry,
    IssueStatusRegistry,
    IssueTypeRegistry,
    JiraProjectRegistry,
    JiraUserRegistry,
)
from src.common.domains.metrics_lizard.registries import (
    FileMetricRegistry,
    LizardMetricsProjectRegistry,
)
from src.common.domains.quality.registries import (
    QualityIssueRegistry,
    QualityProjectRegistry,
)
from src.common.domains.components.registries import ComponentRegistry
from src.common.kernel import EntityKind, EntityRef, Graph
from src.common.people import SourceKind
from src.common.people.unified import UnifiedUserRegistry
from src.enrichment.relations.registries import RelationRegistry
from src.enrichment.tags.registries import ClassifierRegistry, TraitRegistry


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_graph_defaults_every_typed_field_to_empty_concrete_registry():
    g = Graph(project_id="p1")

    # People
    assert isinstance(g.unified_users, UnifiedUserRegistry)
    assert isinstance(g.git_accounts, GitAccountRegistry)
    assert isinstance(g.jira_users, JiraUserRegistry)
    assert isinstance(g.github_users, GitHubUserRegistry)

    # Projects (one per source domain)
    assert isinstance(g.git_projects, GitProjectRegistry)
    assert isinstance(g.jira_projects, JiraProjectRegistry)
    assert isinstance(g.github_projects, GitHubProjectRegistry)
    assert isinstance(g.code_structure_projects, CodeStructureProjectRegistry)
    assert isinstance(g.duplication_projects, DuplicationProjectRegistry)
    assert isinstance(g.quality_projects, QualityProjectRegistry)
    assert isinstance(g.lizard_projects, LizardMetricsProjectRegistry)

    # Git
    assert isinstance(g.commits, CommitRegistry)
    assert isinstance(g.files, FileRegistry)
    assert isinstance(g.changes, ChangeRegistry)
    assert isinstance(g.hunks, HunkRegistry)

    # Jira
    assert isinstance(g.issues, IssueRegistry)
    assert isinstance(g.issue_statuses, IssueStatusRegistry)
    assert isinstance(g.issue_types, IssueTypeRegistry)

    # GitHub
    assert isinstance(g.pull_requests, PullRequestRegistry)
    assert isinstance(g.reviews, ReviewRegistry)
    assert isinstance(g.review_comments, ReviewCommentRegistry)
    assert isinstance(g.github_commits, GitHubCommitRegistry)

    # Code structure
    assert isinstance(g.code_types, CodeTypeRegistry)
    assert isinstance(g.code_methods, CodeMethodRegistry)

    # Quality / duplication / lizard
    assert isinstance(g.duplications, DuplicationPairRegistry)
    assert isinstance(g.quality_issues, QualityIssueRegistry)
    assert isinstance(g.file_metrics, FileMetricRegistry)

    # Enrichment
    assert isinstance(g.components, ComponentRegistry)
    assert isinstance(g.traits, TraitRegistry)
    assert isinstance(g.classifiers, ClassifierRegistry)
    assert isinstance(g.relations, RelationRegistry)

    # All empty
    assert len(g.commits) == 0
    assert len(g.traits) == 0
    assert len(g.relations) == 0


def test_graph_meta_fields_have_sane_defaults():
    g = Graph(project_id="abc")
    assert g.schema_version == 2
    assert g.project_id == "abc"
    assert g.built_at is not None
    # Auto-default to UTC now (within 5s of test run)
    delta = abs((g.built_at - datetime.now(timezone.utc)).total_seconds())
    assert delta < 5


def test_graph_extra_kwargs_forbidden():
    """``extra="forbid"`` rejects unknown construction kwargs."""
    with pytest.raises(Exception):
        Graph(project_id="x", not_a_field=42)


# ---------------------------------------------------------------------------
# registry_for + resolve dispatch
# ---------------------------------------------------------------------------


def _git_setup():
    project = GitProject(id="gp1", name="zep", source=SourceKind.GIT)
    project_ref = project.ref()
    alice = GitAccount(
        id="alice",
        name="Alice",
        project_ref=project_ref,
        email="a@x",
    )
    return project, alice


def test_registry_for_returns_typed_field_for_single_kind():
    g = Graph(project_id="p1")
    assert g.registry_for(EntityKind.COMMIT) is g.commits
    assert g.registry_for(EntityKind.FILE) is g.files
    assert g.registry_for(EntityKind.TRAIT) is g.traits
    assert g.registry_for(EntityKind.RELATION) is g.relations


def test_registry_for_returns_none_for_project_kind():
    """``EntityKind.PROJECT`` maps to 7 typed fields; no single registry
    is returned. Callers iterate :meth:`Graph.project_registries`
    instead.
    """
    g = Graph(project_id="p1")
    assert g.registry_for(EntityKind.PROJECT) is None
    assert len(g.project_registries()) == 7


def test_resolve_roundtrip_through_typed_field():
    project, alice = _git_setup()
    g = Graph(project_id="p1")
    g.git_accounts.add(alice)

    ref = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice")
    resolved = g.resolve(ref)
    assert resolved is not None
    assert resolved.id == "alice"
    assert resolved.name == "Alice"


def test_resolve_returns_none_for_unknown_id():
    g = Graph(project_id="p1")
    missing = g.resolve(EntityRef(kind=EntityKind.GIT_ACCOUNT, id="ghost"))
    assert missing is None


def test_resolve_walks_project_registries_for_project_refs():
    """A ``EntityRef(kind=PROJECT, id=...)`` is resolved by walking every
    project registry in declaration order. The first match wins.
    """
    g = Graph(project_id="p1")
    g.git_projects.add(GitProject(id="gp1", name="zep", source=SourceKind.GIT))
    g.jira_projects.add(JiraProject(id="jp1", name="z", source=SourceKind.JIRA))

    git_ref = EntityRef(kind=EntityKind.PROJECT, id="gp1")
    jira_ref = EntityRef(kind=EntityKind.PROJECT, id="jp1")

    assert g.resolve(git_ref) is not None
    assert g.resolve(git_ref).id == "gp1"
    assert g.resolve(jira_ref) is not None
    assert g.resolve(jira_ref).id == "jp1"

    missing = g.resolve(EntityRef(kind=EntityKind.PROJECT, id="ghost"))
    assert missing is None


# ---------------------------------------------------------------------------
# add_project — multi-registry dispatch
# ---------------------------------------------------------------------------


def test_add_project_routes_by_isinstance():
    g = Graph(project_id="p1")
    git_proj = GitProject(id="gp1", name="g", source=SourceKind.GIT)
    jira_proj = JiraProject(id="jp1", name="j", source=SourceKind.JIRA)
    github_proj = GitHubProject(id="hp1", name="h", source=SourceKind.GITHUB)

    g.add_project(git_proj)
    g.add_project(jira_proj)
    g.add_project(github_proj)

    assert g.git_projects.get("gp1") is git_proj
    assert g.jira_projects.get("jp1") is jira_proj
    assert g.github_projects.get("hp1") is github_proj
    # Other project registries are still empty.
    assert len(g.duplication_projects) == 0
    assert len(g.quality_projects) == 0


# ---------------------------------------------------------------------------
# Legacy ``registries=`` kwarg backwards-compat
# ---------------------------------------------------------------------------


def test_legacy_registries_kwarg_fans_into_typed_field():
    """A ``registries={EntityKind.COMMIT: reg}`` dict still works — the
    model_validator fans it out into the matching typed field.
    """
    reg = CommitRegistry()
    g = Graph(project_id="p1", registries={EntityKind.COMMIT: reg})
    assert g.commits is reg


def test_legacy_registries_property_returns_a_dict_view():
    g = Graph(project_id="p1")
    snapshot = g.registries
    assert isinstance(snapshot, dict)
    assert EntityKind.COMMIT in snapshot
    assert isinstance(snapshot[EntityKind.COMMIT], CommitRegistry)
