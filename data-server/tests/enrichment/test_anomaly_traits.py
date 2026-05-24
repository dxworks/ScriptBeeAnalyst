"""Anomaly trait emission tests — v2 port (Chunk 12).

Restored / re-derived from
``git show f840488^:data-server/tests/enrichment/test_anomaly_traits.py``
and adjacent coverage. The legacy file built a "kitchen-sink" synthetic
graph via ``build_synthetic_graph`` + ``build_jira_fixture`` +
``build_github_fixture`` (now deleted). This v2 port assembles the same
trait coverage in three smaller, per-metric scenarios so each test
exercises exactly one metric at a time.

Covered metrics (Chunk 12):

* :class:`AnomalyComplexityMetric`     — ``anomaly.cohesion.size.DynamicBlob``
* :class:`AnomalyCouplingMetric`       — ``anomaly.structuring.PivotFile``
                                          (``basis="coupling"``)
* :class:`AnomalyQualityIssuesMetric`  — ``anomaly.codesmell.<Cat>.<Rule>``

Deferred families (Chunks 15 / 16): testing / structuring (cochange-basis) /
cohesion (Hibernator etc.) / knowledge — those tests live in
``tests/enrichment/test_a21_file_traits.py`` (under ``xfail``).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.common.domains.metrics_lizard.models import (
    FileMetric,
    LizardMetricsProject,
)
from src.common.domains.quality.models import QualityIssue, QualityProject
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind
from src.enrichment.config import EnrichmentConfig
from src.enrichment.metrics.implementations.anomaly_complexity import (
    AnomalyComplexityMetric,
)
from src.enrichment.metrics.implementations.anomaly_coupling import (
    AnomalyCouplingMetric,
)
from src.enrichment.metrics.implementations.anomaly_quality_issues import (
    AnomalyQualityIssuesMetric,
)
from src.enrichment.relations import Relation, WindowKind
from src.enrichment.tags import TraitFamily

from tests.enrichment.conftest import (
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)


UTC = timezone.utc


# ----------------------------------------------------------------------
# Local helpers
# ----------------------------------------------------------------------
def _lizard_project(graph):
    lp = LizardMetricsProject(id="lp:test", name="lizard", source=SourceKind.GIT)
    graph.lizard_projects.add(lp)
    return lp


def _add_lizard_metric(graph, lp, file_, name: str, value: float) -> None:
    graph.file_metrics.add(FileMetric(
        id=FileMetric.make_id(file_.id, name),
        project_ref=lp.ref(),
        file_ref=file_.ref(),
        metric_name=name,
        value=value,
    ))


def _quality_project(graph, tool: str = "insider"):
    qp = QualityProject(
        id=f"qp:{tool}", name=tool, source=SourceKind.GIT, source_tool=tool,
    )
    graph.quality_projects.add(qp)
    return qp


def _consume(graph, metric):
    """Drain a metric into the graph's traits registry."""
    for emitted in metric.compute(graph, EnrichmentConfig()):
        graph.traits.add(emitted)


# ======================================================================
# AnomalyComplexityMetric — DynamicBlob
# ======================================================================
def test_dynamicblob_fires_above_loc_and_change_thresholds():
    """File with sum_nloc >= 500 AND >=20 changes → DynamicBlob fires."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("dyn-pos")
    alice = make_account("Alice", "alice@example.com", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/blob.py", project.ref())
    graph.files.add(f)

    for i in range(22):
        c = make_commit(f"c_{i}", "feat", alice, now - timedelta(days=i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=50)

    lp = _lizard_project(graph)
    _add_lizard_metric(graph, lp, f, "sum_nloc", 600)
    _add_lizard_metric(graph, lp, f, "max_ccn", 18)
    _add_lizard_metric(graph, lp, f, "avg_ccn", 4.2)

    _consume(graph, AnomalyComplexityMetric())
    traits = graph.traits.of_name("anomaly.cohesion.size.DynamicBlob")
    assert len(traits) == 1
    t = traits[0]
    assert t.family == TraitFamily.COHESION
    assert t.evidence["loc"] == 600
    assert t.evidence["changes"] == 22
    assert t.evidence["max_ccn"] == 18.0
    assert 1.0 <= t.severity <= 10.0


def test_dynamicblob_does_not_fire_below_loc_threshold():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("dyn-low-loc")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/small.py", project.ref())
    graph.files.add(f)
    for i in range(25):
        c = make_commit(f"c{i}", "feat", alice, now - timedelta(days=i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=20)
    lp = _lizard_project(graph)
    _add_lizard_metric(graph, lp, f, "sum_nloc", 100)  # below 500

    _consume(graph, AnomalyComplexityMetric())
    assert graph.traits.of_name("anomaly.cohesion.size.DynamicBlob") == ()


def test_dynamicblob_does_not_fire_below_changes_threshold():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("dyn-low-changes")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/stable.py", project.ref())
    graph.files.add(f)
    # Only 5 changes — below default 20.
    for i in range(5):
        c = make_commit(f"c{i}", "feat", alice, now - timedelta(days=i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=200)
    lp = _lizard_project(graph)
    _add_lizard_metric(graph, lp, f, "sum_nloc", 5000)

    _consume(graph, AnomalyComplexityMetric())
    assert graph.traits.of_name("anomaly.cohesion.size.DynamicBlob") == ()


def test_dynamicblob_severity_scales_with_overshoot():
    """5× LOC overshoot + 3× changes overshoot → severity capped at 10."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("dyn-sev")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/giant.py", project.ref())
    graph.files.add(f)
    for i in range(70):  # 3.5× changes_min=20 → +4 bonus
        c = make_commit(f"c{i}", "feat", alice, now - timedelta(days=i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=10)
    lp = _lizard_project(graph)
    _add_lizard_metric(graph, lp, f, "sum_nloc", 3000)  # 6× loc_min=500 → +5

    _consume(graph, AnomalyComplexityMetric())
    traits = graph.traits.of_name("anomaly.cohesion.size.DynamicBlob")
    assert len(traits) == 1
    assert traits[0].severity == 10.0  # clamped


def test_dynamicblob_skips_files_without_lizard_metrics():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("dyn-no-lizard")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/no_lizard.py", project.ref())
    graph.files.add(f)
    for i in range(25):
        c = make_commit(f"c{i}", "feat", alice, now - timedelta(days=i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=100)
    # No FileMetric rows at all.
    _consume(graph, AnomalyComplexityMetric())
    assert graph.traits.of_name("anomaly.cohesion.size.DynamicBlob") == ()


# ======================================================================
# AnomalyCouplingMetric — PivotFile (basis=coupling)
# ======================================================================
def test_pivotfile_coupling_fires_above_threshold():
    """File with 25 distinct coupling peers exceeds default threshold (10)."""
    graph, project = build_v2_graph("pf-pos")
    pivot = make_file("src/pivot.py", project.ref())
    graph.files.add(pivot)
    for i in range(25):
        peer = make_file(f"src/peer_{i}.py", project.ref())
        graph.files.add(peer)
        graph.relations.add(Relation(
            id=Relation.canonical_id(
                pivot.ref(), peer.ref(), "coupling", WindowKind.LIFETIME,
            ),
            source=pivot.ref(),
            target=peer.ref(),
            relation_kind="coupling",
            window=WindowKind.LIFETIME,
            strength=1.0,
        ))

    _consume(graph, AnomalyCouplingMetric())
    pivots = graph.traits.of_name("anomaly.structuring.PivotFile")
    # The pivot fires; each peer has degree 1 (just the pivot), well
    # below ``pivotfile_cochange_degree_min`` (default 10).
    pivot_traits = [t for t in pivots if t.target == pivot.ref()]
    peer_traits = [t for t in pivots if t.target != pivot.ref()]
    assert len(pivot_traits) == 1
    assert peer_traits == []
    t = pivot_traits[0]
    assert t.family == TraitFamily.STRUCTURING
    assert t.evidence["basis"] == "coupling"
    assert t.evidence["coupling_degree"] == 25
    assert t.severity == 25.0


def test_pivotfile_coupling_does_not_fire_below_threshold():
    """A file with 9 peers (below ``degree_min``=10) must not fire."""
    graph, project = build_v2_graph("pf-low")
    hub = make_file("src/hub.py", project.ref())
    graph.files.add(hub)
    for i in range(9):
        peer = make_file(f"src/p_{i}.py", project.ref())
        graph.files.add(peer)
        graph.relations.add(Relation(
            id=Relation.canonical_id(
                hub.ref(), peer.ref(), "coupling", WindowKind.LIFETIME,
            ),
            source=hub.ref(), target=peer.ref(),
            relation_kind="coupling", window=WindowKind.LIFETIME, strength=1.0,
        ))

    _consume(graph, AnomalyCouplingMetric())
    assert graph.traits.of_name("anomaly.structuring.PivotFile") == ()


def test_pivotfile_coupling_treats_edges_as_undirected():
    """A file with 20 incoming-only coupling edges still hits threshold."""
    graph, project = build_v2_graph("pf-incoming")
    sink = make_file("src/sink.py", project.ref())
    graph.files.add(sink)
    for i in range(20):
        src = make_file(f"src/in_{i}.py", project.ref())
        graph.files.add(src)
        graph.relations.add(Relation(
            id=Relation.canonical_id(
                src.ref(), sink.ref(), "coupling", WindowKind.LIFETIME,
            ),
            source=src.ref(), target=sink.ref(),
            relation_kind="coupling", window=WindowKind.LIFETIME, strength=1.0,
        ))

    _consume(graph, AnomalyCouplingMetric())
    sink_traits = [
        t for t in graph.traits.of_name("anomaly.structuring.PivotFile")
        if t.target == sink.ref()
    ]
    assert len(sink_traits) == 1
    assert sink_traits[0].evidence["coupling_degree"] == 20


# ======================================================================
# AnomalyQualityIssuesMetric — codesmell.<Category>.<Rule>
# ======================================================================
def test_codesmell_trait_per_file_per_rule():
    """Three Insider rows across two files → 3 traits (one per (file, rule))."""
    graph, project = build_v2_graph("qi-basic")
    f_foo = make_file("zeppelin/file/Foo.java", project.ref())
    f_bar = make_file("zeppelin/other/Bar.java", project.ref())
    for f in (f_foo, f_bar):
        graph.files.add(f)
    qp = _quality_project(graph)
    graph.quality_issues.add(QualityIssue(
        id="insider:Foo:StubImplementer:0",
        project_ref=qp.ref(), file_ref=f_foo.ref(),
        rule_id="Stub Implementer", category="Inheritance",
        occurrence_count=1, source_tool="insider",
    ))
    graph.quality_issues.add(QualityIssue(
        id="insider:Foo:CatchTopLevel:0",
        project_ref=qp.ref(), file_ref=f_foo.ref(),
        rule_id="Catch Top-Level Exception", category="Traceability",
        occurrence_count=5, source_tool="insider",
    ))
    graph.quality_issues.add(QualityIssue(
        id="insider:Bar:CatchTopLevel:0",
        project_ref=qp.ref(), file_ref=f_bar.ref(),
        rule_id="Catch Top-Level Exception", category="Traceability",
        occurrence_count=3, source_tool="insider",
    ))

    _consume(graph, AnomalyQualityIssuesMetric())

    foo_traits = graph.traits.for_target(f_foo.ref())
    bar_traits = graph.traits.for_target(f_bar.ref())
    assert len(foo_traits) == 2
    assert len(bar_traits) == 1
    bar_t = bar_traits[0]
    assert bar_t.name == "anomaly.codesmell.Traceability.CatchTopLevelException"
    assert bar_t.evidence["occurrence_count"] == 3
    assert bar_t.evidence["basis"] == "insider"
    assert bar_t.evidence["git_matched"] is True
    assert bar_t.severity == 3.0
    assert bar_t.family == TraitFamily.SMELL


def test_codesmell_unknown_file_marks_git_matched_false():
    """An issue whose file_ref is not in graph.files still tags;
    ``evidence.git_matched`` records the mismatch."""
    graph, project = build_v2_graph("qi-unknown")
    qp = _quality_project(graph)
    unknown_ref = EntityRef(kind=EntityKind.FILE, id="unknown.java")
    graph.quality_issues.add(QualityIssue(
        id="insider:Unknown:R:0",
        project_ref=qp.ref(), file_ref=unknown_ref,
        rule_id="R", category="C",
        occurrence_count=1, source_tool="insider",
    ))
    _consume(graph, AnomalyQualityIssuesMetric())
    t = graph.traits.for_target(unknown_ref)[0]
    assert t.evidence["git_matched"] is False


def test_codesmell_no_traits_when_quality_issues_absent():
    graph, _ = build_v2_graph("qi-empty")
    _consume(graph, AnomalyQualityIssuesMetric())
    assert len(list(graph.traits)) == 0


def test_codesmell_bins_collapse_same_rule_per_file():
    """Two records with the same (file, category, rule) merge into one trait
    with summed occurrence_count."""
    graph, project = build_v2_graph("qi-dup")
    f = make_file("zeppelin/file/Foo.java", project.ref())
    graph.files.add(f)
    qp = _quality_project(graph)
    for i in range(3):
        graph.quality_issues.add(QualityIssue(
            id=f"insider:Foo:R:{i}",
            project_ref=qp.ref(), file_ref=f.ref(),
            rule_id="R", category="C",
            occurrence_count=2, source_tool="insider",
        ))
    _consume(graph, AnomalyQualityIssuesMetric())
    traits = graph.traits.for_target(f.ref())
    assert len(traits) == 1
    t = traits[0]
    assert t.evidence["occurrence_count"] == 6
    assert t.evidence["record_count"] == 3
    assert t.severity == 6.0


def test_codesmell_trait_name_sanitises_segments():
    """Spaces, dots, hyphens in category / rule must not leak into the namespace."""
    graph, project = build_v2_graph("qi-sanitise")
    f = make_file("src/x.java", project.ref())
    graph.files.add(f)
    qp = _quality_project(graph)
    graph.quality_issues.add(QualityIssue(
        id="insider:x:weird:0",
        project_ref=qp.ref(), file_ref=f.ref(),
        rule_id="Catch Top-Level Exception", category="Code.Quality",
        occurrence_count=1, source_tool="insider",
    ))
    _consume(graph, AnomalyQualityIssuesMetric())
    t = graph.traits.for_target(f.ref())[0]
    # Dot in category and spaces / hyphens in rule are stripped.
    assert t.name == "anomaly.codesmell.CodeQuality.CatchTopLevelException"
