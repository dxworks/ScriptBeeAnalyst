"""Restored A2.3 relations regression checklist — v2 port.

Restored from
``git show f840488^:data-server/tests/enrichment/test_relations_a23.py``
and ported to v2 fixtures + entity shapes.

Scope split per Chunk-13 brief:

* **Chunk 13 (this chunk) implements + tests the file-* variants.**
  Those tests live in :mod:`tests.enrichment.test_cochange_file_relations`
  with broader per-builder coverage. The file-* legacy assertions are
  re-expressed here as smoke checks against the pipeline output for
  parity with the legacy regression surface.

* **Chunk 14 will implement the author-* and component-* variants.**
  Those tests are restored here under
  :func:`pytest.mark.xfail(strict=False, reason="depends on Chunk 14 ...")`
  so the checklist surfaces the un-shipped variants without breaking
  the regression suite. The marker drops when Chunk 14 lands.

The legacy A2.3 file used a kitchen-sink ``build_synthetic_graph``
fixture (now deleted); we instead assemble the minimum graph each
assertion needs via the v2 conftest factories.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

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


# Marker shared by Chunk-14 follow-ups.
PENDING_CHUNK_14 = pytest.mark.xfail(
    strict=False,
    reason="depends on Chunk 14 cochange_author_* / cochange_component_*",
)


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

    run_pipeline(g, EnrichmentConfig(time_windowed_cochange_hours=24))
    pairs = {
        tuple(sorted((r.source.id, r.target.id))): r
        for r in g.relations.of_kind("cochange_file_time_windowed")
    }
    assert pairs.get(("src/a.py", "src/b.py")) is not None
    assert pairs[("src/a.py", "src/b.py")].strength >= 1


# ======================================================================
# Author-* family — DEFERRED to Chunk 14
# ======================================================================
@PENDING_CHUNK_14
def test_author_shared_task_prefixes_emits_when_authors_share_jira_prefixes():
    """Parity vs legacy
    ``test_author_shared_task_prefixes_emits_when_authors_share_jira_prefixes``.

    Will pass once :class:`CochangeAuthorSharedTaskPrefixesBuilder` is
    implemented (Chunk 14).
    """
    g, _alice, _bob = _build_two_author_two_file_graph("a23-auth-stp")
    run_pipeline(g, EnrichmentConfig())
    rels = list(g.relations.of_kind("cochange_author_shared_task_prefixes"))
    assert len(rels) >= 1
    assert all(r.strength >= 1 for r in rels)


@PENDING_CHUNK_14
def test_author_time_windowed_counts_commits_inside_window():
    """Parity vs legacy
    ``test_author_time_windowed_counts_commits_inside_window``.

    Will pass once :class:`CochangeAuthorTimeWindowedBuilder` is
    implemented (Chunk 14).
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

    run_pipeline(g, EnrichmentConfig(time_windowed_cochange_hours=24))
    edges = [
        r for r in g.relations.of_kind("cochange_author_time_windowed")
        if {r.source.id, r.target.id} == {alice.id, bob.id}
    ]
    assert len(edges) >= 1
    assert edges[0].strength >= 4


# ======================================================================
# Component-* family — DEFERRED to Chunk 14
# ======================================================================
@PENDING_CHUNK_14
def test_component_cochange_aggregations_emit_when_file_pairs_present():
    """Parity vs legacy ``test_component_aggregations_emit_when_file_pairs_present``.

    Will pass once :class:`CochangeComponentSharedDevsBuilder`,
    :class:`CochangeComponentSharedTaskPrefixesBuilder`, and
    :class:`CochangeComponentTimeWindowedBuilder` are implemented
    (Chunk 14).
    """
    g, _alice, _bob = _build_two_author_two_file_graph("a23-comp")
    run_pipeline(g, EnrichmentConfig())
    for kind in (
        "cochange_component_shared_devs",
        "cochange_component_shared_task_prefixes",
        "cochange_component_time_windowed",
    ):
        rels = list(g.relations.of_kind(kind))
        assert rels, f"missing kind {kind}"
        for r in rels:
            assert r.source != r.target  # no self-loops
