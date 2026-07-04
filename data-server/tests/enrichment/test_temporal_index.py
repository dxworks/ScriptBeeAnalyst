"""Tests for :class:`TemporalIndex` (Phase 2 decision D2)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from src.common.kernel import EntityKind
from src.enrichment.utils.temporal import TemporalIndex

from tests.enrichment.conftest import (
    UTC,
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _seed(graph, project, *, count: int, gap_hours: float):
    """Add ``count`` commits to ``graph``, spaced ``gap_hours`` apart
    starting from a fixed 2024-01-01 anchor. Returns the list of commit
    refs in chronological order."""
    alice = make_account("Alice", "a@x", project.ref())
    graph.git_accounts.add(alice)

    f = make_file("src/foo.py", project.ref())
    graph.files.add(f)

    anchor = UTC.localize if hasattr(UTC, "localize") else None  # noqa: F841
    from datetime import datetime
    base = datetime(2024, 1, 1, tzinfo=UTC)

    refs = []
    for i in range(count):
        when = base + timedelta(hours=gap_hours * i)
        c = make_commit(f"c{i}", "msg", alice, when, project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=1)
        refs.append(c.ref())
    return refs, base


# ----------------------------------------------------------------------
# Construction + sorting
# ----------------------------------------------------------------------
def test_temporal_index_from_empty_graph_is_empty() -> None:
    graph, _ = build_v2_graph()
    ti = TemporalIndex.from_graph(graph)
    assert len(ti) == 0
    assert ti.count(EntityKind.COMMIT) == 0
    assert ti.commits_in_window(0.0, 1e12) == []
    # ``pairs_within`` returns an iterator — materialise.
    assert list(ti.pairs_within(hours=24)) == []


def test_temporal_index_sorts_commits_by_timestamp() -> None:
    """Insertion order on the registry must NOT bias the index — the
    sorted list is what enables bisect lookups."""
    graph, project = build_v2_graph()
    alice = make_account("Alice", "a@x", project.ref())
    graph.git_accounts.add(alice)

    from datetime import datetime
    # Add commits out of chronological order on purpose.
    times = [
        datetime(2024, 6, 1, tzinfo=UTC),
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 3, 15, tzinfo=UTC),
    ]
    for i, when in enumerate(times):
        c = make_commit(f"c{i}", "msg", alice, when, project.ref())
        graph.commits.add(c)

    ti = TemporalIndex.from_graph(graph)
    # The window covering all three should return them in chronological
    # order, not insertion order.
    refs = ti.commits_in_window(
        start_ts_unix=times[1].timestamp(),
        end_ts_unix=times[0].timestamp() + 1.0,
    )
    assert [r.id for r in refs] == ["c1", "c2", "c0"]


# ----------------------------------------------------------------------
# commits_in_window — empty / single / boundary cases
# ----------------------------------------------------------------------
def test_commits_in_window_empty_when_outside_range() -> None:
    graph, project = build_v2_graph()
    refs, base = _seed(graph, project, count=3, gap_hours=24)
    ti = TemporalIndex.from_graph(graph)

    # Window strictly before any commit.
    early = (base - timedelta(days=10)).timestamp()
    assert ti.commits_in_window(early - 100, early) == []


def test_commits_in_window_half_open_at_upper_bound() -> None:
    """An end_ts equal to a commit ts excludes that commit (half-open)."""
    graph, project = build_v2_graph()
    refs, base = _seed(graph, project, count=3, gap_hours=24)
    ti = TemporalIndex.from_graph(graph)

    second_ts = (base + timedelta(hours=24)).timestamp()
    # Include the first commit, exclude the second.
    found = ti.commits_in_window(base.timestamp(), second_ts)
    assert [r.id for r in found] == ["c0"]


def test_commits_in_window_inclusive_at_lower_bound() -> None:
    graph, project = build_v2_graph()
    refs, base = _seed(graph, project, count=3, gap_hours=24)
    ti = TemporalIndex.from_graph(graph)

    found = ti.commits_in_window(base.timestamp(), (base + timedelta(days=10)).timestamp())
    assert [r.id for r in found] == ["c0", "c1", "c2"]


def test_commits_in_window_reversed_range_returns_empty() -> None:
    graph, project = build_v2_graph()
    refs, base = _seed(graph, project, count=2, gap_hours=24)
    ti = TemporalIndex.from_graph(graph)
    assert ti.commits_in_window(end_ts_unix=base.timestamp(),
                                start_ts_unix=base.timestamp() + 1e6) == []


# ----------------------------------------------------------------------
# pairs_within — no pair / one pair / many pairs
# ----------------------------------------------------------------------
def test_pairs_within_yields_nothing_when_gap_exceeds_window() -> None:
    graph, project = build_v2_graph()
    _seed(graph, project, count=3, gap_hours=48)  # 48h gaps
    ti = TemporalIndex.from_graph(graph)
    # 24h window — no two commits qualify.
    assert list(ti.pairs_within(hours=24)) == []


def test_pairs_within_finds_single_pair() -> None:
    graph, project = build_v2_graph()
    _seed(graph, project, count=3, gap_hours=72)  # 72h gaps
    # Add one extra commit 1h after c1 — pairs with c1 but NOT with c0/c2.
    alice = list(graph.git_accounts)[0]
    f = list(graph.files)[0]
    from datetime import datetime
    when = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=72 + 1)
    extra = make_commit("extra", "msg", alice, when, project.ref())
    graph.commits.add(extra)
    add_change(graph, extra, f, added=1)

    ti = TemporalIndex.from_graph(graph)
    pairs = list(ti.pairs_within(hours=24))
    # The only pair within 24h is (c1, extra). Order: earlier first.
    assert len(pairs) == 1
    assert {p.id for p in pairs[0]} == {"c1", "extra"}


def test_pairs_within_finds_all_cross_pairs() -> None:
    graph, project = build_v2_graph()
    # 4 commits in a tight 12h cluster — C(4,2) = 6 pairs.
    _seed(graph, project, count=4, gap_hours=2)
    ti = TemporalIndex.from_graph(graph)
    pairs = list(ti.pairs_within(hours=24))
    assert len(pairs) == 6
    # Every pair must be ordered (earlier, later) by id (c0..c3 are also
    # chronological in this seed).
    for earlier, later in pairs:
        assert earlier.id < later.id


# ----------------------------------------------------------------------
# Graph.ensure_temporal_index caching
# ----------------------------------------------------------------------
def test_ensure_temporal_index_caches_instance() -> None:
    graph, project = build_v2_graph()
    _seed(graph, project, count=2, gap_hours=24)
    first = graph.ensure_temporal_index()
    second = graph.ensure_temporal_index()
    assert first is second
