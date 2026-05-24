"""Restored A2.3 relations regression checklist — v2 port.

Restored from
``git show f840488^:data-server/tests/enrichment/test_relations_a23.py``
and ported to v2 fixtures + entity shapes.

Scope split per Chunk-13 / Chunk-14 brief:

* **Chunk 13 implemented + tests the file-* variants.** Those tests
  live in :mod:`tests.enrichment.test_cochange_file_relations` with
  broader per-builder coverage. The file-* legacy assertions are
  re-expressed here as smoke checks against the pipeline output for
  parity with the legacy regression surface.

* **Chunk 14 (this chunk) implements the author-* and component-*
  variants.** Tests that were xfailed under ``PENDING_CHUNK_14`` are
  now passing assertions — the marker alias is gone and the helper
  test fixture was tightened (component test needs a mapping that
  splits the two files into distinct components, otherwise the
  heuristic resolver groups both under ``"src"`` → self-loop → drop).

The legacy A2.3 file used a kitchen-sink ``build_synthetic_graph``
fixture (now deleted); we instead assemble the minimum graph each
assertion needs via the v2 conftest factories.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import run_pipeline

from tests.enrichment.conftest import (
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)


UTC = timezone.utc


# ----------------------------------------------------------------------
# Common fixture: small graph with two files co-touched by two authors
# ----------------------------------------------------------------------
def _build_two_author_two_file_graph(name: str):
    g, p = build_v2_graph(name)
    alice = make_account("Alice", "alice@example.com", p.ref())
    bob = make_account("Bob", "bob@example.com", p.ref())
    g.git_accounts.add(alice)
    g.git_accounts.add(bob)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)
    now = datetime.now(UTC)
    # Two cochange commits — one per author — touching both files.
    c1 = make_commit("c1", "PROJ-1: refactor", alice, now - timedelta(days=2), p.ref())
    c2 = make_commit("c2", "PROJ-1: more", bob, now - timedelta(hours=1), p.ref())
    g.commits.add(c1)
    g.commits.add(c2)
    add_change(g, c1, fa, added=5)
    add_change(g, c1, fb, added=3)
    add_change(g, c2, fa, added=2)
    add_change(g, c2, fb, added=1)
    return g, alice, bob


# ======================================================================
# File-* family — implemented in Chunk 13
# ======================================================================
def test_file_shared_devs_emits_for_pairs_with_overlapping_authors():
    """Parity assertion vs legacy
    ``test_file_shared_devs_emits_for_pairs_with_overlapping_authors``."""
    g, _alice, _bob = _build_two_author_two_file_graph("a23-sd")
    run_pipeline(g, EnrichmentConfig())

    pairs = {
        tuple(sorted((r.source.id, r.target.id))): r
        for r in g.relations.of_kind("cochange_file_shared_devs")
    }
    target = ("src/a.py", "src/b.py")
    assert target in pairs
    assert pairs[target].strength >= 1


def test_file_shared_task_prefixes_emits_when_message_carries_key():
    """Parity vs legacy ``test_file_shared_task_prefixes_emits_when_jira_linked``.

    In v2 the prefix surface flows through commit-message extraction
    rather than Jira-issue linkage (see :mod:`...task_prefix` D3).
    """
    g, _alice, _bob = _build_two_author_two_file_graph("a23-stp")
    run_pipeline(g, EnrichmentConfig())

    pairs = {
        tuple(sorted((r.source.id, r.target.id))): r
        for r in g.relations.of_kind("cochange_file_shared_task_prefixes")
    }
    target = ("src/a.py", "src/b.py")
    assert target in pairs
    assert pairs[target].strength >= 1


def test_file_time_windowed_emits_for_close_in_time_distinct_commits():
    """Parity vs legacy
    ``test_file_time_windowed_emits_for_close_in_time_distinct_commits``."""
    g, p = build_v2_graph("a23-tw")
    alice = make_account("Alice", "alice@example.com", p.ref())
    g.git_accounts.add(alice)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)

    now = datetime.now(UTC)
    c1 = make_commit("c1", "m", alice, now - timedelta(hours=1), p.ref())
    c2 = make_commit("c2", "m", alice, now - timedelta(minutes=30), p.ref())
    g.commits.add(c1)
    g.commits.add(c2)
    add_change(g, c1, fa, added=5)
    add_change(g, c2, fb, added=5)

    cfg = EnrichmentConfig(
        time_windowed_cochange_hours=24,
        time_windowed_cochange_file_min_count=1,
    )
    g.__dict__["config"] = cfg
    run_pipeline(g, cfg)
    pairs = {
        tuple(sorted((r.source.id, r.target.id))): r
        for r in g.relations.of_kind("cochange_file_time_windowed")
    }
    assert pairs.get(("src/a.py", "src/b.py")) is not None
    assert pairs[("src/a.py", "src/b.py")].strength >= 1


# ======================================================================
# Author-* family — implemented in Chunk 14
# ======================================================================
def test_author_shared_task_prefixes_emits_when_authors_share_jira_prefixes():
    """Parity vs legacy
    ``test_author_shared_task_prefixes_emits_when_authors_share_jira_prefixes``.

    Both commits carry ``PROJ-1`` in the message, so Alice and Bob share
    a single task prefix.
    """
    g, alice, bob = _build_two_author_two_file_graph("a23-auth-stp")
    run_pipeline(g, EnrichmentConfig())
    rels = [
        r
        for r in g.relations.of_kind("cochange_author_shared_task_prefixes")
        if {r.source.id, r.target.id} == {alice.id, bob.id}
    ]
    assert len(rels) >= 1
    assert all(r.strength >= 1 for r in rels)


def test_author_time_windowed_counts_commits_inside_window():
    """Parity vs legacy
    ``test_author_time_windowed_counts_commits_inside_window``.

    Five commits — two recent (alice, bob within 30 min), two ~20h ago
    (alice, bob within ~1h of each other), and one 200 days ago. The
    24h window should pair:
      * (c1, c2) — alice/bob within 30 min  → 1 cross-pair
      * (c3, c4) — alice/bob within ~1h     → 1 cross-pair
      * (c1, c4) — alice(now-1h)/bob(now-21h) ~20h apart → 1 cross-pair
      * (c2, c4) — bob(now-30m)/bob(now-21h) → same-author, skipped
      * (c1, c3) — alice(now-1h)/alice(now-20h) → same-author, skipped
      * (c2, c3) — bob(now-30m)/alice(now-20h) ~19.5h → 1 cross-pair
    Expected strength: 4 cross-author commit pairs inside the window.
    """
    g, p = build_v2_graph("a23-auth-tw")
    alice = make_account("Alice", "alice@example.com", p.ref())
    bob = make_account("Bob", "bob@example.com", p.ref())
    g.git_accounts.add(alice)
    g.git_accounts.add(bob)
    f = make_file("src/x.py", p.ref())
    g.files.add(f)

    now = datetime.now(UTC)
    c1 = make_commit("c1", "m", alice, now - timedelta(hours=1), p.ref())
    c2 = make_commit("c2", "m", bob,   now - timedelta(minutes=30), p.ref())
    c3 = make_commit("c3", "m", alice, now - timedelta(hours=20), p.ref())
    c4 = make_commit("c4", "m", bob,   now - timedelta(hours=21), p.ref())
    c5 = make_commit("c5", "m", alice, now - timedelta(days=200), p.ref())
    for c in (c1, c2, c3, c4, c5):
        g.commits.add(c)
        add_change(g, c, f, added=1)

    cfg = EnrichmentConfig(time_windowed_cochange_hours=24)
    g.__dict__["config"] = cfg
    run_pipeline(g, cfg)
    edges = [
        r for r in g.relations.of_kind("cochange_author_time_windowed")
        if {r.source.id, r.target.id} == {alice.id, bob.id}
    ]
    assert len(edges) >= 1
    assert edges[0].strength >= 4


# ======================================================================
# Component-* family — implemented in Chunk 14
# ======================================================================
def _components_mapping_path(tmp_path) -> str:
    """Write a 2-component mapping that splits ``src/a.py`` and
    ``src/b.py`` into distinct ``comp_a`` / ``comp_b`` components — so
    the aggregator emits cross-component edges instead of self-loops.
    """
    payload = {
        "comp_a": {"path_prefix": "src/a"},
        "comp_b": {"path_prefix": "src/b"},
    }
    p = tmp_path / "components.json"
    p.write_text(json.dumps(payload))
    return str(p)


def _build_two_author_two_file_graph_recent(name: str):
    """Variant of :func:`_build_two_author_two_file_graph` where both
    commits land within an hour of each other — needed so the upstream
    :class:`CochangeFileTimeWindowedBuilder` emits an edge inside the
    default 24h window. The base helper places commits ~47h apart, which
    is fine for shared-devs / shared-task-prefixes but starves the
    time-windowed variant.
    """
    g, p = build_v2_graph(name)
    alice = make_account("Alice", "alice@example.com", p.ref())
    bob = make_account("Bob", "bob@example.com", p.ref())
    g.git_accounts.add(alice)
    g.git_accounts.add(bob)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)
    now = datetime.now(UTC)
    c1 = make_commit("c1", "PROJ-1: refactor", alice, now - timedelta(hours=2), p.ref())
    c2 = make_commit("c2", "PROJ-1: more", bob, now - timedelta(hours=1), p.ref())
    g.commits.add(c1)
    g.commits.add(c2)
    add_change(g, c1, fa, added=5)
    add_change(g, c1, fb, added=3)
    add_change(g, c2, fa, added=2)
    add_change(g, c2, fb, added=1)
    return g, alice, bob


def test_component_cochange_aggregations_emit_when_file_pairs_present(tmp_path):
    """Parity vs legacy
    ``test_component_aggregations_emit_when_file_pairs_present``.

    The two ``src/a.py`` + ``src/b.py`` files resolve to distinct
    components ``comp_a`` / ``comp_b`` via an explicit mapping so the
    aggregator emits cross-component edges. Same fixture exercises all
    four component cochange variants because the file-* builders
    upstream emit cochange / shared_devs / shared_task_prefixes /
    time_windowed edges for the same pair.

    Note: builders read ``graph.config`` (not the ``config`` argument to
    :func:`run_pipeline`, which only flows to metrics). Tests attach the
    config via ``__dict__`` to bypass Pydantic's ``extra="forbid"``.
    """
    g, _alice, _bob = _build_two_author_two_file_graph_recent("a23-comp")
    cfg = EnrichmentConfig(
        components_mapping_path=_components_mapping_path(tmp_path),
        time_windowed_cochange_hours=24,
        time_windowed_cochange_file_min_count=1,
        time_windowed_cochange_component_min_count=1,
    )
    g.__dict__["config"] = cfg
    run_pipeline(g, cfg)
    for kind in (
        "cochange_component",
        "cochange_component_shared_devs",
        "cochange_component_shared_task_prefixes",
        "cochange_component_time_windowed",
    ):
        rels = list(g.relations.of_kind(kind))
        assert rels, f"missing kind {kind}"
        for r in rels:
            assert r.source != r.target  # no self-loops
            assert r.strength > 0
