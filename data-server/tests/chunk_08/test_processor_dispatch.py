"""Processor dispatch — Transformer → typed Graph registry routing.

Feeds the processor a synthetic entity bundle for each domain. For each
bundle:

* :func:`get_transformer` returns the matching :class:`Transformer`.
* :meth:`Transformer.transform` produces a :class:`TransformResult`.
* :func:`apply_transform_result` routes each ``(kind, bucket)`` into
  the matching typed registry on :class:`Graph`.

Then runs ``run_pipeline(graph, DEFAULT_CONFIG)`` against the bundled
graph and asserts ≥25 builders + ≥14 metrics were attempted (mirroring
Chunk-7's autoload test).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.common.domains.git.models import (
    Change,
    ChangeType,
    Commit,
    File,
    GitAccount,
    GitProject,
    Hunk,
    LineChange,
    LineOperation,
)
from src.common.domains.jira.models import (
    Issue,
    IssueStatus,
    IssueType,
    JiraProject,
    JiraUser,
)
from src.common.domains.github.models import (
    GitHubCommit,
    GitHubProject,
    GitHubUser,
    PullRequest,
)
from src.common.kernel import EntityKind, Graph
from src.common.people import SourceKind
from src.enrichment.config import DEFAULT_CONFIG
from src.common.domains.app_inspector import AppInspectorTransformer
from src.processor import (
    _TRANSFORMERS,
    apply_transform_result,
    build_graph_from_bundles,
    get_transformer,
)


# ---------------------------------------------------------------------------
# Bundle factories — minimal entity bundles per domain
# ---------------------------------------------------------------------------


def _git_bundle():
    project = GitProject(id="gp1", name="zep", source=SourceKind.GIT)
    project_ref = project.ref()
    alice = GitAccount(
        id="alice",
        name="Alice",
        project_ref=project_ref,
        email="a@x",
    )
    file = File(id="src/app.py", project_ref=project_ref, path="src/app.py", extension="py")
    commit = Commit(
        id="abc",
        sha="abc",
        project_ref=project_ref,
        message="init",
        author_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        committer_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_ref=alice.ref(),
        committer_ref=alice.ref(),
    )
    change = Change(
        id=Change.make_id(commit.id, file.path, file.path),
        commit_ref=commit.ref(),
        file_ref=file.ref(),
        change_type=ChangeType.ADD,
        old_path=file.path,
        new_path=file.path,
    )
    hunk = Hunk(
        id=Hunk.make_id(change.id, 0),
        change_ref=change.ref(),
        ordinal=0,
        line_changes=[
            LineChange(
                operation=LineOperation.ADD,
                line_number=1,
                commit_ref=commit.ref(),
            )
        ],
    )
    return {
        "project": project,
        "accounts": [alice],
        "commits": [commit],
        "files": [file],
        "changes": [change],
        "hunks": [hunk],
    }


def _jira_bundle():
    project = JiraProject(id="jp1", name="JIR", source=SourceKind.JIRA)
    project_ref = project.ref()
    status = IssueStatus(id="open", project_ref=project_ref, name="Open", category="new")
    typ = IssueType(id="bug", project_ref=project_ref, name="Bug")
    bob = JiraUser(
        id="bob",
        name="Bob",
        project_ref=project_ref,
        key="bob",
    )
    issue = Issue(
        id="JIR-1",
        project_ref=project_ref,
        key="JIR-1",
        summary="t",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        status_ref=status.ref(),
        type_ref=typ.ref(),
        reporter_ref=bob.ref(),
    )
    return {
        "project": project,
        "users": [bob],
        "issues": [issue],
        "issue_statuses": [status],
        "issue_types": [typ],
    }


def _github_bundle():
    project = GitHubProject(id="hp1", name="H", source=SourceKind.GITHUB)
    project_ref = project.ref()
    alice = GitHubUser(
        id="alice-gh",
        name="Alice",
        project_ref=project_ref,
        url="https://github/alice",
        login="alice",
    )
    pr = PullRequest(
        id="1",
        project_ref=project_ref,
        number=1,
        title="t",
        state="OPEN",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_ref=alice.ref(),
    )
    gh_commit = GitHubCommit(
        id="abc1234",
        sha="abc1234",
        pull_request_ref=pr.ref(),
        date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        message="m",
        author_ref=alice.ref(),
    )
    return {
        "project": project,
        "users": [alice],
        "pull_requests": [pr],
        "commits": [gh_commit],
    }


# ---------------------------------------------------------------------------
# Tests — per-domain dispatch
# ---------------------------------------------------------------------------


def test_dispatch_git_bundle_lands_in_typed_registries():
    transformer = get_transformer(SourceKind.GIT)
    result = transformer.transform(_git_bundle())

    g = Graph(project_id="t-git")
    apply_transform_result(g, result)

    assert len(g.git_projects) == 1
    assert len(g.git_accounts) == 1
    assert len(g.commits) == 1
    assert len(g.files) == 1
    assert len(g.changes) == 1
    assert len(g.hunks) == 1
    # Other domains untouched.
    assert len(g.issues) == 0
    assert len(g.pull_requests) == 0


def test_dispatch_jira_bundle_lands_in_typed_registries():
    transformer = get_transformer(SourceKind.JIRA)
    result = transformer.transform(_jira_bundle())

    g = Graph(project_id="t-jira")
    apply_transform_result(g, result)

    assert len(g.jira_projects) == 1
    assert len(g.jira_users) == 1
    assert len(g.issues) == 1
    assert len(g.issue_statuses) == 1
    assert len(g.issue_types) == 1


def test_dispatch_github_bundle_lands_in_typed_registries():
    transformer = get_transformer(SourceKind.GITHUB)
    result = transformer.transform(_github_bundle())

    g = Graph(project_id="t-github")
    apply_transform_result(g, result)

    assert len(g.github_projects) == 1
    assert len(g.github_users) == 1
    assert len(g.pull_requests) == 1
    assert len(g.github_commits) == 1


def test_dispatch_multiple_sources_share_one_graph():
    """All three sources feed into the same Graph instance via separate
    transformers — registries are populated independently and resolve()
    routes correctly across them.
    """
    g = Graph(project_id="multi")
    for source, bundle in [
        (SourceKind.GIT, _git_bundle()),
        (SourceKind.JIRA, _jira_bundle()),
        (SourceKind.GITHUB, _github_bundle()),
    ]:
        transformer = get_transformer(source)
        result = transformer.transform(bundle)
        apply_transform_result(g, result)

    assert len(g.commits) == 1
    assert len(g.issues) == 1
    assert len(g.pull_requests) == 1
    # Three project registries each got one entry.
    assert len(g.git_projects) == 1
    assert len(g.jira_projects) == 1
    assert len(g.github_projects) == 1


# ---------------------------------------------------------------------------
# build_graph_from_bundles + run_pipeline integration
# ---------------------------------------------------------------------------


def test_build_graph_from_bundles_runs_phase_a_only():
    """End-to-end: feed a multi-source bundle to
    :func:`build_graph_from_bundles` and assert the Phase-A half of the
    pipeline attempted every non-people builder + metric.

    UnifiedUsers redesign §H (task P4.A): /build runs Phase A only.
    Phase B (people-side: ``coauthor`` / ``ownership`` / ``pr.reviewer``
    / cochange-author-* / ``author.classifiers`` / ``anomaly.knowledge``
    / ``anomaly.cohesion`` / ``anomaly.structuring``) moves to the
    /finalize endpoint where it runs against the rebound graph. The
    expected attempted counts here are the Phase A half of the catalog
    (≥18 builders + ≥11 metrics out of the full 25 + 15).
    """
    graph, result = build_graph_from_bundles(
        "smoke",
        {
            SourceKind.GIT: [_git_bundle()],
            SourceKind.JIRA: [_jira_bundle()],
            SourceKind.GITHUB: [_github_bundle()],
        },
        config=DEFAULT_CONFIG,
    )

    # Graph populated.
    assert graph.project_id == "smoke"
    assert len(graph.commits) == 1
    assert len(graph.issues) == 1
    assert len(graph.pull_requests) == 1

    # Pipeline attempted every Phase-A step. Successful runs + errors
    # together cover the Phase-A subset (Chunk 7 deferred stubs raise
    # NotImplementedError, which the pipeline catches into ``errors``).
    builders_attempted = len(result.builders_run) + sum(
        1 for e in result.errors if e.step == "builder"
    )
    metrics_attempted = len(result.metrics_run) + sum(
        1 for e in result.errors if e.step == "metric"
    )
    assert builders_attempted >= 18, (
        f"Expected ≥18 Phase-A builders attempted, got {builders_attempted}"
    )
    assert metrics_attempted >= 11, (
        f"Expected ≥11 Phase-A metrics attempted, got {metrics_attempted}"
    )

    # And none of the Phase-B steps fired at build time.
    from src.enrichment.pipeline import (
        phase_b_metric_names,
        phase_b_relation_kinds,
    )

    phase_b_builders = phase_b_relation_kinds()
    phase_b_metrics = phase_b_metric_names()
    fired_builder_names = set(result.builders_run) | {
        e.name for e in result.errors if e.step == "builder"
    }
    fired_metric_names = set(result.metrics_run) | {
        e.name for e in result.errors if e.step == "metric"
    }
    assert not (fired_builder_names & phase_b_builders), (
        f"Phase-B builders leaked into /build: "
        f"{fired_builder_names & phase_b_builders}"
    )
    assert not (fired_metric_names & phase_b_metrics), (
        f"Phase-B metrics leaked into /build: "
        f"{fired_metric_names & phase_b_metrics}"
    )


def test_transformers_map_includes_app_inspector():
    """``_TRANSFORMERS`` dispatch table wires ``SourceKind.APP_INSPECTOR``
    to :class:`AppInspectorTransformer`, mirroring the quality / lizard
    entries (Task 7 step 5)."""
    assert _TRANSFORMERS[SourceKind.APP_INSPECTOR] is AppInspectorTransformer


def test_apply_transform_result_dispatches_project_via_isinstance():
    """A TransformResult's ``project`` field is routed by
    :meth:`Graph.add_project`, which uses ``isinstance`` to pick the
    right typed registry. We exercise both git + jira to confirm the
    dispatch table isn't biased.
    """
    transformer_git = get_transformer(SourceKind.GIT)
    result_git = transformer_git.transform(_git_bundle())

    transformer_jira = get_transformer(SourceKind.JIRA)
    result_jira = transformer_jira.transform(_jira_bundle())

    g = Graph(project_id="dispatch")
    apply_transform_result(g, result_git)
    apply_transform_result(g, result_jira)

    assert g.git_projects.get("gp1") is not None
    assert g.jira_projects.get("jp1") is not None
    # Other project registries are untouched.
    assert len(g.github_projects) == 0
    assert len(g.duplication_projects) == 0
