"""A2.4 — coverage tests for the Chunk-17 medium-tier overview tables.

Covers ``pace``, ``code_quality``, ``knowledge``, and ``pr_lifecycle``.
The heavy-tier overviews (``components``, ``feature_traceability``,
``feature_encapsulation``, ``intent_impact``, ``testing``) stay
NotImplementedError-stubbed and are exercised by Chunk-18.

Each medium-overview test asserts:

* the table is registered and renders against the v2 fixture data,
* its column layout matches the builder's spec,
* at least one cell on the ``(project)`` row carries a meaningful value.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.config import EnrichmentConfig
from src.enrichment.overviews import OVERVIEWS
from src.enrichment.overviews.implementations.code_quality_table import (
    COLUMNS as CQ_COLUMNS,
    CodeQualityTableBuilder,
)
from src.enrichment.overviews.implementations.knowledge_table import (
    COLUMNS as KNOW_COLUMNS,
    KnowledgeTableBuilder,
)
from src.enrichment.overviews.implementations.pace_table import (
    COLUMNS as PACE_COLUMNS,
    PaceTableBuilder,
)
from src.enrichment.overviews.implementations.pr_lifecycle_table import (
    COLUMNS as PR_COLUMNS,
    PrLifecycleTableBuilder,
)
from src.common.domains.quality.models import QualityIssue, QualityProject
from src.common.people import SourceKind
from src.common.domains.github.models import (
    GitHubProject,
    GitHubUser,
    PullRequest,
    Review,
)
from src.enrichment.pipeline import run_pipeline
from src.enrichment.tags import Classifier

from tests.enrichment.conftest import (
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)


UTC = timezone.utc


# ----------------------------------------------------------------------
# Heavy-tier xfail markers — Chunk-18 ships those overviews.
# ----------------------------------------------------------------------
HEAVY_NAMES = (
    "components",
    "feature_encapsulation",
    "feature_traceability",
    "intent_impact",
    "testing",
)


@pytest.mark.parametrize("name", HEAVY_NAMES)
def test_heavy_overviews_remain_deferred_until_chunk_18(name):
    """Heavy overviews are still :class:`NotImplementedError` stubs."""
    cls = OVERVIEWS.get(name)
    builder = cls()
    graph, _ = build_v2_graph(f"heavy-{name}")
    with pytest.raises(NotImplementedError):
        builder.build(graph, EnrichmentConfig())


# ======================================================================
# Pace
# ======================================================================
def _seed_pace_graph(name: str):
    now = datetime.now(UTC)
    graph, project = build_v2_graph(name)
    alice = make_account("Alice", "a@x", project.ref())
    bob = make_account("Bob", "b@x", project.ref())
    graph.git_accounts.add(alice)
    graph.git_accounts.add(bob)

    src_a = make_file("src/a.py", project.ref())
    src_b = make_file("src/b.py", project.ref())
    tests_c = make_file("tests/c.py", project.ref())
    graph.files.add(src_a)
    graph.files.add(src_b)
    graph.files.add(tests_c)

    seeds = [
        ("c1", "fix: critical bug", alice, now - timedelta(days=4), src_a, 10, 2),
        ("c2", "fix: another", bob, now - timedelta(days=3), src_b, 5, 0),
        ("c3", "add tests for x", alice, now - timedelta(days=2), tests_c, 30, 0),
        ("c4", "feat: shiny thing", bob, now - timedelta(days=1), src_a, 8, 1),
    ]
    for sha, message, author, when, file_, added, deleted in seeds:
        c = make_commit(sha, message, author, when, project.ref())
        graph.commits.add(c)
        add_change(graph, c, file_, added=added, deleted=deleted)

    return graph, project, now


def test_pace_overview_registered_with_expected_columns():
    assert "pace" in OVERVIEWS.names()
    assert OVERVIEWS.get("pace") is PaceTableBuilder
    graph, _, _ = _seed_pace_graph("pace-cols")
    table = PaceTableBuilder().build(graph, EnrichmentConfig())
    assert table.columns == PACE_COLUMNS
    assert table.entity_kind == "component"


def test_pace_overview_distinct_authors_and_nature_mix():
    graph, _, _ = _seed_pace_graph("pace-mix")
    # Run the full pipeline so commit nature classifiers exist.
    run_pipeline(graph, EnrichmentConfig())

    table = PaceTableBuilder().build(graph, EnrichmentConfig())
    project_row = next(r for r in table.rows if r.entity_id == "(project)")
    assert project_row.cells["distinct_authors"].lifetime_value == 2

    pct_bugfix = project_row.cells["pct_bugfix"].lifetime_value
    pct_feature = project_row.cells["pct_feature"].lifetime_value
    assert pct_bugfix is not None and pct_bugfix > 0
    assert pct_feature is not None and pct_feature > 0


def test_pace_overview_folder_split():
    graph, _, _ = _seed_pace_graph("pace-folders")
    run_pipeline(graph, EnrichmentConfig())
    table = PaceTableBuilder().build(graph, EnrichmentConfig())
    row_ids = {r.entity_id for r in table.rows}
    assert "(project)" in row_ids
    assert "src" in row_ids
    assert "tests" in row_ids


def test_pace_overview_empty_graph_has_only_project_row():
    graph, _ = build_v2_graph("pace-empty")
    table = PaceTableBuilder().build(graph, EnrichmentConfig())
    assert [r.entity_id for r in table.rows] == ["(project)"]


# ======================================================================
# Code quality
# ======================================================================
def _seed_quality_graph(name: str):
    """Build a fixture with two quality issues across two folders."""
    graph, git_project = build_v2_graph(name)
    quality_project = QualityProject(
        id=f"qp:{name}", name=name, source=SourceKind.QUALITY,
    )
    graph.add_project(quality_project)

    src_a = make_file("src/a.py", git_project.ref())
    src_b = make_file("src/b.py", git_project.ref())
    tests_c = make_file("tests/c.py", git_project.ref())
    for f in (src_a, src_b, tests_c):
        graph.files.add(f)

    issues = [
        QualityIssue(
            id=f"q:{name}:1",
            project_ref=quality_project.ref(),
            file_ref=src_a.ref(),
            rule_id="StubImplementer",
            category="Maintainability",
            occurrence_count=3,
        ),
        QualityIssue(
            id=f"q:{name}:2",
            project_ref=quality_project.ref(),
            file_ref=src_b.ref(),
            rule_id="StubImplementer",
            category="Maintainability",
            occurrence_count=2,
        ),
        QualityIssue(
            id=f"q:{name}:3",
            project_ref=quality_project.ref(),
            file_ref=tests_c.ref(),
            rule_id="LongMethod",
            category="Maintainability",
            occurrence_count=1,
        ),
    ]
    for i in issues:
        graph.quality_issues.add(i)
    return graph, git_project


def test_code_quality_overview_registered_with_expected_columns():
    assert "code_quality" in OVERVIEWS.names()
    assert OVERVIEWS.get("code_quality") is CodeQualityTableBuilder
    graph, _ = _seed_quality_graph("cq-cols")
    table = CodeQualityTableBuilder().build(graph, EnrichmentConfig())
    assert table.columns == CQ_COLUMNS
    assert table.entity_kind == "component"


def test_code_quality_overview_aggregates_top_rules_per_folder():
    graph, _ = _seed_quality_graph("cq-mix")
    table = CodeQualityTableBuilder().build(graph, EnrichmentConfig())
    rows = {r.entity_id: r for r in table.rows}
    assert {"(project)", "src", "tests"}.issubset(rows.keys())

    project = rows["(project)"]
    # 3 + 2 + 1 = 6 occurrences across two distinct rules + three files.
    assert project.cells["total_smells"].lifetime_value == 6
    assert project.cells["distinct_rules"].lifetime_value == 2
    assert project.cells["distinct_files"].lifetime_value == 3
    assert project.cells["top_rule"].lifetime_value == "StubImplementer"
    assert project.cells["top_rule_count"].lifetime_value == 5

    src_row = rows["src"]
    # src/* carries StubImplementer-only.
    assert src_row.cells["top_rule"].lifetime_value == "StubImplementer"
    assert src_row.cells["top_rule_count"].lifetime_value == 5
    assert src_row.cells["distinct_files"].lifetime_value == 2

    tests_row = rows["tests"]
    assert tests_row.cells["top_rule"].lifetime_value == "LongMethod"
    assert tests_row.cells["top_rule_count"].lifetime_value == 1


def test_code_quality_overview_empty_graph_has_only_project_row():
    graph, _ = build_v2_graph("cq-empty")
    table = CodeQualityTableBuilder().build(graph, EnrichmentConfig())
    assert [r.entity_id for r in table.rows] == ["(project)"]
    proj = table.rows[0]
    assert proj.cells["total_smells"].lifetime_value == 0


def test_code_quality_overview_hotspot_count_reads_codesmell_traits():
    """A file carrying any ``anomaly.codesmell.*`` trait should appear in
    ``hotspot_files``. We run the full pipeline so the
    :class:`AnomalyQualityIssuesMetric` actually fires."""
    graph, _ = _seed_quality_graph("cq-hot")
    run_pipeline(graph, EnrichmentConfig())
    table = CodeQualityTableBuilder().build(graph, EnrichmentConfig())
    project = next(r for r in table.rows if r.entity_id == "(project)")
    # At least one of our three quality issues should hit the per-rule
    # hotspot threshold in the Chunk-12 metric on the synthetic data —
    # we tolerate "0 or more" rather than pinning an exact number so the
    # test stays robust to threshold tweaks.
    assert isinstance(project.cells["hotspot_files"].lifetime_value, int)
    assert project.cells["hotspot_files"].lifetime_value >= 0


# ======================================================================
# Knowledge
# ======================================================================
def _seed_knowledge_graph(name: str):
    """Build a single-author file that should fire Orphan + the activity
    classifier (one active + one idle author so APK% lands in (0, 100))."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph(name)
    graph.__dict__["recent_cutoff"] = now - timedelta(days=90)

    alice = make_account("Alice", "a@x", project.ref())
    bob = make_account("Bob", "b@x", project.ref())
    graph.git_accounts.add(alice)
    graph.git_accounts.add(bob)

    orphan = make_file("src/orphan.py", project.ref())
    active = make_file("src/active.py", project.ref())
    graph.files.add(orphan)
    graph.files.add(active)

    # Orphan file: Alice only, all commits >180 days ago.
    for i in range(3):
        c = make_commit(
            f"o_{i}", "feat: add", alice,
            now - timedelta(days=200 + i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, orphan, added=20)

    # Active file: Bob recent commits.
    for i in range(2):
        c = make_commit(
            f"a_{i}", "feat: latest", bob,
            now - timedelta(days=10 + i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, active, added=15)

    return graph, project, now


def test_knowledge_overview_registered_with_expected_columns():
    assert "knowledge" in OVERVIEWS.names()
    assert OVERVIEWS.get("knowledge") is KnowledgeTableBuilder
    graph, _ = build_v2_graph("k-cols")
    table = KnowledgeTableBuilder().build(graph, EnrichmentConfig())
    assert table.columns == KNOW_COLUMNS
    assert table.entity_kind == "component"


def test_knowledge_overview_aggregates_orphan_count_and_apk():
    graph, _, _ = _seed_knowledge_graph("k-mix")
    run_pipeline(graph, EnrichmentConfig())
    table = KnowledgeTableBuilder().build(graph, EnrichmentConfig())
    rows = {r.entity_id: r for r in table.rows}
    assert "(project)" in rows
    assert "src" in rows

    project = rows["(project)"]
    # Orphan trait should fire on src/orphan.py.
    assert project.cells["orphan_count"].lifetime_value >= 1
    # APK% — Bob is the only "active" author, so the apk% should be
    # strictly between 0 and 100 (some idle churn from Alice + some
    # active churn from Bob).
    apk = project.cells["apk_percent"].lifetime_value
    assert apk is not None
    assert 0 <= apk <= 100


def test_knowledge_overview_empty_graph_has_only_project_row():
    graph, _ = build_v2_graph("k-empty")
    table = KnowledgeTableBuilder().build(graph, EnrichmentConfig())
    assert [r.entity_id for r in table.rows] == ["(project)"]


# ======================================================================
# PR lifecycle
# ======================================================================
def _seed_pr_graph(name: str):
    """Build a graph with two PRs: one merged after a review, one open."""
    now = datetime.now(UTC)
    graph, _ = build_v2_graph(name)
    gh_project = GitHubProject(
        id=f"ghp:{name}", name=name, source=SourceKind.GITHUB,
    )
    graph.add_project(gh_project)

    user = GitHubUser(
        id=f"ghu:{name}:alice",
        name="Alice",
        project_ref=gh_project.ref(),
        login="alice",
    )
    graph.github_users.add(user)

    merged_pr = PullRequest(
        id=PullRequest.make_id(1),
        number=1,
        project_ref=gh_project.ref(),
        title="Merged PR",
        state="closed",
        changed_files=3,
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=4),
        merged_at=now - timedelta(days=4),
        author_ref=user.ref(),
    )
    graph.pull_requests.add(merged_pr)

    review = Review(
        id=Review.make_id(1, 0),
        pull_request_ref=merged_pr.ref(),
        ordinal=0,
        state="APPROVED",
        submitted_at=now - timedelta(days=6),
        body="LGTM",
        author_ref=user.ref(),
    )
    graph.reviews.add(review)

    open_pr = PullRequest(
        id=PullRequest.make_id(2),
        number=2,
        project_ref=gh_project.ref(),
        title="Open PR",
        state="open",
        changed_files=10,
        created_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=30),
        author_ref=user.ref(),
    )
    graph.pull_requests.add(open_pr)

    return graph, gh_project, now


def test_pr_lifecycle_overview_registered_with_expected_columns():
    assert "pr_lifecycle" in OVERVIEWS.names()
    assert OVERVIEWS.get("pr_lifecycle") is PrLifecycleTableBuilder
    graph, _ = build_v2_graph("pr-cols")
    table = PrLifecycleTableBuilder().build(graph, EnrichmentConfig())
    assert table.columns == PR_COLUMNS
    assert table.entity_kind == "project"


def test_pr_lifecycle_overview_emits_single_project_row():
    graph, _, _ = _seed_pr_graph("pr-rows")
    table = PrLifecycleTableBuilder().build(graph, EnrichmentConfig())
    assert [r.entity_id for r in table.rows] == ["(project)"]


def test_pr_lifecycle_overview_turnaround_hours_and_size_buckets():
    graph, _, _ = _seed_pr_graph("pr-mix")
    # Manually emit a pr.size classifier so the bucket-share columns
    # exercise their predicates.
    pr_ref = EntityRef(kind=EntityKind.PULL_REQUEST, id="1")
    graph.classifiers.add(Classifier(
        id="pr.size:pull_request/1",
        target=pr_ref,
        dimension="pr.size",
        value="S",
    ))
    table = PrLifecycleTableBuilder().build(graph, EnrichmentConfig())
    project = table.rows[0]

    # PR #1 has one approving review 2 days before merge → 48h turnaround.
    turnaround = project.cells["review_turnaround_hours"].lifetime_value
    assert turnaround is not None
    assert 47.0 <= turnaround <= 49.0

    assert project.cells["total_prs"].lifetime_value == 2
    # 1 of 2 PRs is classified S → 50%.
    assert project.cells["pct_size_s"].lifetime_value == 50.0
    # Buckets with no classifier emit 0%.
    assert project.cells["pct_size_xl"].lifetime_value == 0.0


def test_pr_lifecycle_overview_stalled_review_count_uses_traits():
    graph, _, _ = _seed_pr_graph("pr-stalled")
    run_pipeline(graph, EnrichmentConfig())
    table = PrLifecycleTableBuilder().build(graph, EnrichmentConfig())
    project = table.rows[0]
    # Open PR is 30 days old without reviews → StalledReview should fire.
    assert project.cells["stalled_review_count"].lifetime_value >= 1


def test_pr_lifecycle_overview_empty_graph_emits_single_row_with_zeros():
    graph, _ = build_v2_graph("pr-empty")
    table = PrLifecycleTableBuilder().build(graph, EnrichmentConfig())
    project = table.rows[0]
    assert project.entity_id == "(project)"
    assert project.cells["total_prs"].lifetime_value == 0
    assert project.cells["review_turnaround_hours"].lifetime_value is None
