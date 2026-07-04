"""Tests for :class:`AnomalyTimezoneMetric` — Chunk 16.

ZoneCrossroad and ConcurrentZoneCrossroad are derived purely from each
commit's ``author_date.utcoffset()``; no external utils. Tests build
small synthetic graphs and assert on the emitted :class:`Trait` rows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.config import EnrichmentConfig
from src.enrichment.metrics import METRICS
from src.enrichment.pipeline import run_pipeline

from tests.enrichment.conftest import (
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)


UTC = timezone.utc


def _traits_for_file(graph, file_id: str) -> tuple:
    return graph.traits.for_target(
        EntityRef(kind=EntityKind.FILE, id=file_id)
    )


def _trait_names(traits) -> set[str]:
    return {t.name for t in traits}


def _run(graph) -> None:
    run_pipeline(graph, EnrichmentConfig())


# ----------------------------------------------------------------------
# Registry wiring
# ----------------------------------------------------------------------
def test_anomaly_timezone_is_registered():
    assert any(m.name == "anomaly.timezone" for m in METRICS.all())


def test_anomaly_timezone_metadata():
    cls = next(m for m in METRICS.all() if m.name == "anomaly.timezone")
    assert {
        "anomaly.cohesion.ZoneCrossroad",
        "anomaly.cohesion.ConcurrentZoneCrossroad",
    }.issubset(set(cls.outputs.emits_traits))
    assert "zonecrossroad_min_zone_commits" in cls.config_fields


# ----------------------------------------------------------------------
# ZoneCrossroad — two or more significant zones
# ----------------------------------------------------------------------
def test_zonecrossroad_fires_on_two_significant_zones():
    base = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    utc_plus_2 = timezone(timedelta(hours=2))
    graph, project = build_v2_graph("tz1")
    alice = make_account("Alice", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/tz1.py", project.ref())
    graph.files.add(f)

    # 10 commits at UTC, then 10 commits at UTC+2 → both zones significant.
    for i in range(10):
        c = make_commit(
            f"u_{i}", "feat", alice,
            base + timedelta(days=i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, f, added=1)
    for i in range(10):
        c = make_commit(
            f"p_{i}", "feat", alice,
            (base + timedelta(days=100 + i)).astimezone(utc_plus_2),
            project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, f, added=1)

    _run(graph)
    traits = _traits_for_file(graph, "src/tz1.py")
    zc = next(t for t in traits if t.name == "anomaly.cohesion.ZoneCrossroad")
    assert zc.evidence["zones_with_activity"] == 2
    assert zc.evidence["total_zones_seen"] == 2
    assert zc.severity == 2.0


def test_zonecrossroad_suppressed_when_only_one_zone_significant():
    """Primary zone has 20 commits (passes the file gate and is significant),
    secondary zone has only 3 — below per-zone significance threshold."""
    base = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    utc_plus_2 = timezone(timedelta(hours=2))
    graph, project = build_v2_graph("tz2")
    alice = make_account("Alice", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/tz2.py", project.ref())
    graph.files.add(f)

    for i in range(20):
        c = make_commit(
            f"u_{i}", "feat", alice,
            base + timedelta(days=i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, f, added=1)
    for i in range(3):
        c = make_commit(
            f"p_{i}", "feat", alice,
            (base + timedelta(days=200 + i)).astimezone(utc_plus_2),
            project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, f, added=1)

    _run(graph)
    traits = _traits_for_file(graph, "src/tz2.py")
    assert "anomaly.cohesion.ZoneCrossroad" not in _trait_names(traits)


def test_zonecrossroad_respects_min_file_commits_gate():
    """File-level pre-filter mirrors dx (``ZoneCrossroad.java:29``):
    files below ``zonecrossroad_min_file_commits`` skip the metric
    entirely, even when each zone individually meets ``min_zone_commits``.

    The test uses an explicit config (``min_file_commits=20``,
    ``min_zone_commits=5``) to isolate the FILE gate from the per-zone gate.
    A 15-commit / 25-commit pair built with the same shape produces
    different outcomes purely because of the file gate.
    """
    import dataclasses
    base = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    utc_plus_2 = timezone(timedelta(hours=2))
    cfg = dataclasses.replace(
        EnrichmentConfig(),
        zonecrossroad_min_file_commits=20,
        zonecrossroad_min_zone_commits=5,
    )

    def _build(name: str, n_utc: int, n_plus: int):
        graph, project = build_v2_graph(name)
        alice = make_account("Alice", "a@x", project.ref())
        graph.git_accounts.add(alice)
        f = make_file(f"src/{name}.py", project.ref())
        graph.files.add(f)
        for i in range(n_utc):
            c = make_commit(
                f"u_{i}", "feat", alice,
                base + timedelta(days=i), project.ref(),
            )
            graph.commits.add(c)
            add_change(graph, c, f, added=1)
        for i in range(n_plus):
            c = make_commit(
                f"p_{i}", "feat", alice,
                (base + timedelta(days=100 + i)).astimezone(utc_plus_2),
                project.ref(),
            )
            graph.commits.add(c)
            add_change(graph, c, f, added=1)
        return graph

    # 15 total (8 + 7): each zone ≥ ``min_zone_commits=5`` so both are
    # significant — but total < file gate (20) → metric skips the file.
    graph_below = _build("below", 8, 7)
    run_pipeline(graph_below, cfg)
    assert "anomaly.cohesion.ZoneCrossroad" not in _trait_names(
        _traits_for_file(graph_below, "src/below.py")
    )

    # 25 total (13 + 12): identical zone-shape, above the file gate → fires.
    graph_above = _build("above", 13, 12)
    run_pipeline(graph_above, cfg)
    assert "anomaly.cohesion.ZoneCrossroad" in _trait_names(
        _traits_for_file(graph_above, "src/above.py")
    )


def test_zonecrossroad_no_fire_on_single_zone_file():
    """All commits in UTC → no crossroad anywhere, no concurrent periods."""
    base = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    graph, project = build_v2_graph("tz3")
    alice = make_account("Alice", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/tz3.py", project.ref())
    graph.files.add(f)

    for i in range(20):
        c = make_commit(
            f"u_{i}", "feat", alice,
            base + timedelta(days=i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, f, added=1)

    _run(graph)
    traits = _traits_for_file(graph, "src/tz3.py")
    names = _trait_names(traits)
    assert "anomaly.cohesion.ZoneCrossroad" not in names
    assert "anomaly.cohesion.ConcurrentZoneCrossroad" not in names


# ----------------------------------------------------------------------
# ConcurrentZoneCrossroad — multi-zone activity inside the same period
# ----------------------------------------------------------------------
def test_concurrent_zonecrossroad_fires_on_overlapping_zones():
    """Same (year, month) sees commits in two zones → at least one
    concurrent period; ZoneCrossroad must also fire."""
    utc_plus_2 = timezone(timedelta(hours=2))
    graph, project = build_v2_graph("tz4")
    alice = make_account("Alice", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/tz4.py", project.ref())
    graph.files.add(f)

    # 10 UTC commits + 10 UTC+2 commits, all in the SAME month.
    for i in range(10):
        c = make_commit(
            f"u_{i}", "feat", alice,
            datetime(2026, 1, 5 + (i % 5), 9, tzinfo=UTC), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, f, added=1)
    for i in range(10):
        c = make_commit(
            f"p_{i}", "feat", alice,
            datetime(2026, 1, 15 + (i % 5), 11, tzinfo=utc_plus_2),
            project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, f, added=1)

    _run(graph)
    traits = _traits_for_file(graph, "src/tz4.py")
    names = _trait_names(traits)
    assert "anomaly.cohesion.ZoneCrossroad" in names
    czc = next(
        t for t in traits if t.name == "anomaly.cohesion.ConcurrentZoneCrossroad"
    )
    assert czc.evidence["concurrent_periods"] >= 1


def test_concurrent_severity_clamped_at_max():
    """Many concurrent periods → severity tops out at 10."""
    utc_plus_2 = timezone(timedelta(hours=2))
    graph, project = build_v2_graph("tz5")
    alice = make_account("Alice", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/tz5.py", project.ref())
    graph.files.add(f)

    # 12 months with commits in two zones each — 12 concurrent periods,
    # well above 2 * strict_threshold (=10).
    for month in range(1, 13):
        for i in range(5):
            c = make_commit(
                f"u_{month}_{i}", "feat", alice,
                datetime(2026, month, 1 + i, 9, tzinfo=UTC), project.ref(),
            )
            graph.commits.add(c)
            add_change(graph, c, f, added=1)
        for i in range(5):
            c = make_commit(
                f"p_{month}_{i}", "feat", alice,
                datetime(2026, month, 10 + i, 11, tzinfo=utc_plus_2),
                project.ref(),
            )
            graph.commits.add(c)
            add_change(graph, c, f, added=1)

    _run(graph)
    traits = _traits_for_file(graph, "src/tz5.py")
    czc = next(
        t for t in traits if t.name == "anomaly.cohesion.ConcurrentZoneCrossroad"
    )
    assert czc.severity == 10.0
