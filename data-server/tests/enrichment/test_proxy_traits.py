"""Phase 3 proxy traits — Supernova (cohesion.size) + TestOrphan (testing).

Restored from ``git show f840488^:data-server/tests/enrichment/test_proxy_traits.py``
and ported to the v2 :class:`Graph` surface. Mirrors the legacy intent:

* **Supernova** — net-churn proxy on a file. Tag carries
  ``is_proxy=True`` and an evidence note clarifying the "not absolute
  LOC" caveat.
* **TestOrphan** — production file with no commit-cochange to any
  test-role file. Tag is proxy-flagged with a short note.

Both metrics ship in Chunk 15 — Supernova in :class:`AnomalyCohesionMetric`
(15a coordination + size sub-families), TestOrphan in
:class:`AnomalyTestingMetric`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.enrichment.config import EnrichmentConfig
from src.enrichment.metrics.implementations.anomaly_cohesion import (
    AnomalyCohesionMetric,
)
from src.enrichment.metrics.implementations.anomaly_testing import (
    AnomalyTestingMetric,
)
from src.enrichment.metrics.implementations.commit_classifiers import (
    CommitClassifierMetric,
)
from src.enrichment.metrics.implementations.file_classifiers import (
    FileClassifierMetric,
)
from src.enrichment.tags import Classifier, Trait

from tests.enrichment.conftest import (
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)


UTC = timezone.utc


def _consume(graph, metric, config=None) -> None:
    cfg = config if config is not None else EnrichmentConfig()
    for emitted in metric.compute(graph, cfg):
        if isinstance(emitted, Trait):
            graph.traits.add(emitted)
        elif isinstance(emitted, Classifier):
            graph.classifiers.add(emitted)


def test_supernova_fires_above_threshold_with_proxy_evidence():
    """Net-churn over threshold → Supernova with is_proxy and net-churn note."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("supernova")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/owner.py", project.ref())
    graph.files.add(f)
    for i in range(10):
        c = make_commit(f"c_{i}", "feat", alice,
                        now - timedelta(days=5 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=100, deleted=0)

    _consume(graph, AnomalyCohesionMetric(),
             EnrichmentConfig(supernova_net_churn_min=100))
    traits = graph.traits.for_target(f.ref())
    sn = next(t for t in traits if t.name == "anomaly.cohesion.size.Supernova")
    assert sn.is_proxy is True
    assert sn.evidence["proxy"] is True
    assert "net-churn" in sn.evidence["note"]
    assert sn.evidence["net_churn"] >= 100


def test_supernova_does_not_fire_under_threshold():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("supernova-low")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/owner.py", project.ref())
    graph.files.add(f)
    for i in range(3):
        c = make_commit(f"c_{i}", "feat", alice,
                        now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=10)
    _consume(graph, AnomalyCohesionMetric(),
             EnrichmentConfig(supernova_net_churn_min=10**9))
    assert graph.traits.of_name("anomaly.cohesion.size.Supernova") == ()


def test_test_orphan_fires_on_production_file_without_test_cochange():
    """Production file with several commits, no cochange to a test file → TestOrphan."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("orphan")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    prod = make_file("src/prod.py", project.ref())
    tf = make_file("tests/test_prod.py", project.ref())
    for ff in (prod, tf):
        graph.files.add(ff)

    for i in range(4):
        c = make_commit(f"c_{i}", f"feat: prod {i}", alice,
                        now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, prod, added=10)
    tc = make_commit("ct", "test: init", alice, now - timedelta(days=2), project.ref())
    graph.commits.add(tc)
    add_change(graph, tc, tf, added=5)

    _consume(graph, FileClassifierMetric())
    _consume(graph, CommitClassifierMetric())
    _consume(graph, AnomalyTestingMetric())

    traits = graph.traits.for_target(prod.ref())
    to = next((t for t in traits if t.name == "anomaly.testing.TestOrphan"), None)
    assert to is not None
    assert to.is_proxy is True
    assert to.evidence["cochange_test_count"] == 0


def test_test_orphan_suppressed_when_project_has_no_test_files():
    """Zero test-role files → TestOrphan stays silent."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("orphan-empty")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    prod = make_file("src/prod.py", project.ref())
    graph.files.add(prod)
    for i in range(4):
        c = make_commit(f"c_{i}", "feat", alice,
                        now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, prod, added=10)
    _consume(graph, FileClassifierMetric())
    _consume(graph, CommitClassifierMetric())
    _consume(graph, AnomalyTestingMetric())
    assert graph.traits.of_name("anomaly.testing.TestOrphan") == ()


def test_test_orphan_suppressed_when_threshold_raised():
    """Cochange count above threshold (any non-zero) → no trait."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("orphan-thr")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    prod = make_file("src/prod.py", project.ref())
    tf = make_file("tests/test_prod.py", project.ref())
    for ff in (prod, tf):
        graph.files.add(ff)
    for i in range(5):
        c = make_commit(f"c_{i}", "feat", alice,
                        now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, prod, added=10)
        add_change(graph, c, tf, added=4)
    _consume(graph, FileClassifierMetric())
    _consume(graph, CommitClassifierMetric())
    _consume(graph, AnomalyTestingMetric())
    assert graph.traits.of_name("anomaly.testing.TestOrphan") == ()
