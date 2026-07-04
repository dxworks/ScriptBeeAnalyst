"""Cohesion-family trait emission — v2 port (Chunk 15a).

Restored / re-derived from
``git show f840488^:data-server/tests/enrichment/test_cohesion_traits.py``.
The legacy file built a "cohesion synth" graph with Bazaar, Cathedral,
and Pulsar fixtures inside one ``GitProject``; v2 builds three smaller
per-trait graphs so each scenario isolates one signal.

Coverage:

* ``anomaly.cohesion.coordination.Bazaar``      — many distinct recent authors.
* ``anomaly.cohesion.coordination.Cathedral``   — one dominant recent author.
* ``anomaly.cohesion.coordination.Pulsar``      — high lifetime CV of gaps.
* ``anomaly.cohesion.coordination.Flicker``     — high recent-window CV of gaps.

The legacy file also asserted ``anomaly.knowledge.SharedKnowledge`` on
the bazaar fixture; that trait belongs to the **knowledge** family and
ships in Chunk 16 — covered separately in ``test_a21_file_traits.py``.

The activity sub-family (Hibernator / Awakening / Erosion) is deferred
to Chunk 15b; xfail-marked tests live in ``test_a21_file_traits.py``
under :data:`PENDING_COHESION_ACTIVITY`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.enrichment.config import EnrichmentConfig
from src.enrichment.metrics.implementations.anomaly_cohesion import (
    AnomalyCohesionMetric,
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


def test_bazaar_emitted_on_many_authors_recent():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("bazaar")
    devs = [make_account(f"Dev{i}", f"d{i}@x", project.ref()) for i in range(6)]
    for d in devs:
        graph.git_accounts.add(d)
    f = make_file("src/bazaar.py", project.ref())
    graph.files.add(f)
    for i, d in enumerate(devs):
        c = make_commit(f"b_{i}", f"chore: bazaar {i}", d,
                        now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)

    _consume(graph, AnomalyCohesionMetric())
    bz = next(
        t for t in graph.traits.for_target(f.ref())
        if t.name == "anomaly.cohesion.coordination.Bazaar"
    )
    assert bz.evidence["distinct_authors_recent"] >= bz.evidence["threshold"]


def test_cathedral_emitted_on_dominant_recent_author():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("cathedral")
    devs = [make_account(f"Dev{i}", f"d{i}@x", project.ref()) for i in range(2)]
    for d in devs:
        graph.git_accounts.add(d)
    f = make_file("src/cath.py", project.ref())
    graph.files.add(f)
    # Dev0 does 5 recent → dominance 1.0 (or 5/5 if Dev1 absent recently).
    for i in range(5):
        c = make_commit(f"c_{i}", f"refactor: pass {i}", devs[0],
                        now - timedelta(days=15 + i * 2), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=8)
    _consume(graph, AnomalyCohesionMetric())
    cath = next(
        t for t in graph.traits.for_target(f.ref())
        if t.name == "anomaly.cohesion.coordination.Cathedral"
    )
    assert cath.evidence["dominance_ratio"] >= cath.evidence["threshold"]


def test_pulsar_emitted_on_bursty_intervals():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("pulsar")
    devs = [make_account(f"D{i}", f"d{i}@x", project.ref()) for i in range(4)]
    for d in devs:
        graph.git_accounts.add(d)
    f = make_file("src/pulsar.py", project.ref())
    graph.files.add(f)
    # Two tight clusters separated by a wide gap → CV >> 1.
    pulses = [(200, 0), (199, 1), (198, 2), (197, 3),
              (40, 0), (39, 1), (38, 2)]
    for i, (days_back, dev_idx) in enumerate(pulses):
        c = make_commit(f"p_{i}", f"feat: burst {i}", devs[dev_idx],
                        now - timedelta(days=days_back), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=15, deleted=5)
    _consume(graph, AnomalyCohesionMetric())
    p = next(
        t for t in graph.traits.for_target(f.ref())
        if t.name == "anomaly.cohesion.coordination.Pulsar"
    )
    assert p.evidence["interval_cv"] >= p.evidence["threshold"]
    assert p.evidence["commits"] >= 6


def test_pulsar_min_intervals_threshold_is_config_driven():
    """Raising pulsar_min_intervals beyond the fixture's intervals suppresses Pulsar."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("pulsar-suppressed")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/p.py", project.ref())
    graph.files.add(f)
    for i in range(7):
        c = make_commit(f"c_{i}", "feat", alice,
                        now - timedelta(days=200 - i * 30), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)
    _consume(graph, AnomalyCohesionMetric(),
             EnrichmentConfig(pulsar_min_intervals=999))
    assert graph.traits.of_name("anomaly.cohesion.coordination.Pulsar") == ()


def test_flicker_emitted_on_recent_window_volatility():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("flicker")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/fl.py", project.ref())
    graph.files.add(f)
    days_back = [80, 79, 78, 77, 5, 4]  # tight cluster + late tight pair
    for i, d in enumerate(days_back):
        c = make_commit(f"c_{i}", "feat", alice, now - timedelta(days=d), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)
    _consume(graph, AnomalyCohesionMetric())
    t = next(
        x for x in graph.traits.for_target(f.ref())
        if x.name == "anomaly.cohesion.coordination.Flicker"
    )
    assert t.evidence["recent_interval_cv"] >= t.evidence["threshold"]
