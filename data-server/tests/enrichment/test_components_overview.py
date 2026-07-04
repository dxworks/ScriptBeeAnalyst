"""Tests for the Chunk-18 :class:`ComponentsTableBuilder`.

Covers per-component rollup using the
``component_membership`` relations emitted by
:class:`ComponentResolverMetric` plus :class:`FileMetric` (Lizard) +
trait + classifier reads.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.common.domains.metrics_lizard.models import FileMetric, LizardMetricsProject
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind
from src.enrichment.config import EnrichmentConfig
from src.enrichment.overviews import OVERVIEWS
from src.enrichment.overviews.implementations.components_table import (
    COLUMNS as COMP_COLUMNS,
    ComponentsTableBuilder,
)
from src.enrichment.pipeline import run_pipeline

from tests.enrichment.conftest import (
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)


UTC = timezone.utc


def _seed_two_component_graph(name: str):
    """Three files split across two top-folders (``src/owner`` +
    ``src/buggy``); enough commits for the rollup math to be visible."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph(name)
    graph.__dict__["recent_cutoff"] = now - timedelta(days=30)

    alice = make_account("Alice", "a@x", project.ref())
    bob = make_account("Bob", "b@x", project.ref())
    graph.git_accounts.add(alice)
    graph.git_accounts.add(bob)

    owner_file = make_file("src/owner/owner.py", project.ref())
    buggy_file = make_file("src/buggy/bug.py", project.ref())
    helper_file = make_file("src/buggy/helper.py", project.ref())
    for f in (owner_file, buggy_file, helper_file):
        graph.files.add(f)

    # Alice dominates owner.py (≥80% churn over ≥2 distinct authors so
    # BusFactor1 fires); Bob contributes one tiny edit so the metric
    # passes the ``busfactor1_min_distinct_authors >= 2`` gate.
    for i in range(9):
        c = make_commit(
            f"o_{i}", "feat: owner change", alice,
            now - timedelta(days=10 + i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, owner_file, added=50)
    c_bob_owner = make_commit(
        "o_bob", "chore: typo", bob,
        now - timedelta(days=20), project.ref(),
    )
    graph.commits.add(c_bob_owner)
    add_change(graph, c_bob_owner, owner_file, added=1)

    # Buggy: Alice/Bob mix, several bugfixes.
    seeds = [
        ("b1", "fix: cant divide", alice, now - timedelta(days=5), buggy_file, 8, 1),
        ("b2", "fix: more", bob, now - timedelta(days=4), buggy_file, 4, 0),
        ("b3", "fix: edge", alice, now - timedelta(days=3), helper_file, 6, 1),
        ("b4", "feat: add", bob, now - timedelta(days=2), helper_file, 10, 0),
    ]
    for sha, msg, author, when, file_, added, deleted in seeds:
        c = make_commit(sha, msg, author, when, project.ref())
        graph.commits.add(c)
        add_change(graph, c, file_, added=added, deleted=deleted)

    return graph, project, now


def _add_lizard_metric(graph, project_ref, file_ref, metric_name, value):
    try:
        lizard_project = next(iter(graph.lizard_projects))
    except StopIteration:
        lizard_project = LizardMetricsProject(
            id=f"lp:{project_ref.id}", name="lizard",
            source=SourceKind.LIZARD,
        )
        graph.add_project(lizard_project)
    graph.file_metrics.add(FileMetric(
        id=FileMetric.make_id(file_ref.id, metric_name),
        project_ref=lizard_project.ref(),
        file_ref=file_ref,
        metric_name=metric_name,
        value=float(value),
    ))


def test_components_overview_registered_with_expected_columns():
    assert "components" in OVERVIEWS.names()
    assert OVERVIEWS.get("components") is ComponentsTableBuilder
    graph, _ = build_v2_graph("comp-cols")
    table = ComponentsTableBuilder().build(graph, EnrichmentConfig())
    assert table.columns == COMP_COLUMNS
    assert table.entity_kind == "component"


def test_components_overview_empty_graph_emits_only_project_row():
    graph, _ = build_v2_graph("comp-empty")
    table = ComponentsTableBuilder().build(graph, EnrichmentConfig())
    assert [r.entity_id for r in table.rows] == ["(project)"]
    proj = table.rows[0]
    assert proj.cells["file_count"].lifetime_value == 0
    assert proj.cells["total_loc"].lifetime_value is None


def test_components_overview_aggregates_per_component():
    graph, _, _ = _seed_two_component_graph("comp-mix")
    run_pipeline(graph, EnrichmentConfig())
    table = ComponentsTableBuilder().build(graph, EnrichmentConfig())
    by_id = {r.entity_id: r for r in table.rows}
    assert "(project)" in by_id
    # Top-folder fallback: ``src`` is the single component (heuristic
    # mode — no explicit mapping configured here).
    assert "src" in by_id

    project = by_id["(project)"]
    assert project.cells["file_count"].lifetime_value == 3
    # 9 alice-owner + 1 bob-owner + 4 buggy = 14 distinct commits.
    assert project.cells["commit_count"].lifetime_value == 14
    assert project.cells["distinct_authors"].lifetime_value == 2
    # 3 bugfix-message commits out of 14 → ~0.21.
    bugfix = project.cells["bugfix_ratio"].lifetime_value
    assert bugfix is not None
    assert 0 < bugfix < 1
    assert project.cells["bus_factor_1_files"].lifetime_value >= 1


def test_components_overview_reads_lizard_loc_and_max_ccn():
    graph, project, _ = _seed_two_component_graph("comp-lizard")
    # Attach Lizard metrics so the LOC + complexity columns surface.
    files_by_id = {f.id: f for f in graph.files}
    _add_lizard_metric(graph, project.ref(), files_by_id["src/owner/owner.py"].ref(), "sum_nloc", 400)
    _add_lizard_metric(graph, project.ref(), files_by_id["src/owner/owner.py"].ref(), "max_ccn", 18)
    _add_lizard_metric(graph, project.ref(), files_by_id["src/buggy/bug.py"].ref(), "sum_nloc", 120)
    _add_lizard_metric(graph, project.ref(), files_by_id["src/buggy/bug.py"].ref(), "max_ccn", 9)

    run_pipeline(graph, EnrichmentConfig())
    table = ComponentsTableBuilder().build(graph, EnrichmentConfig())
    project_row = next(r for r in table.rows if r.entity_id == "(project)")
    # 400 + 120 = 520 NLOC across the two metric-bearing files.
    assert project_row.cells["total_loc"].lifetime_value == 520.0
    # 520 / 2 = 260 avg.
    assert project_row.cells["avg_loc_per_file"].lifetime_value == 260.0
    # max_ccn is the running max across the two files.
    assert project_row.cells["max_ccn"].lifetime_value == 18.0
