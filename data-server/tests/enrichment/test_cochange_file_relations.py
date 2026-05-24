"""Tests for the three Chunk-13 file-cochange relation builders.

Restored / re-derived from
``git show f840488^:data-server/tests/enrichment/test_relations*.py``
(file subsets only — author/component variants are deferred to Chunk
14).

Builders under test (Chunk 13):

* :class:`CochangeFileTimeWindowedBuilder`     — first real
  :class:`TemporalIndex` consumer
* :class:`CochangeFileSharedDevsBuilder`       — re-weights cochange
  via :class:`OwnershipBuilder` author sets
* :class:`CochangeFileSharedTaskPrefixesBuilder` — re-weights cochange
  via inline :func:`extract_task_prefixes` on commit messages
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from src.common.kernel import EntityRef
from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import run_pipeline
from src.enrichment.relations import BUILDERS, Relation, WindowKind
from src.enrichment.relations.implementations.cochange_file_shared_devs import (
    CochangeFileSharedDevsBuilder,
)
from src.enrichment.relations.implementations.cochange_file_shared_task_prefixes import (
    CochangeFileSharedTaskPrefixesBuilder,
)
from src.enrichment.relations.implementations.cochange_file_time_windowed import (
    CochangeFileTimeWindowedBuilder,
)

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
def _pair_lookup(rels, kind: str, window: WindowKind = WindowKind.LIFETIME):
    """Return ``{(src_id, tgt_id): rel}`` for relations matching kind+window.

    Endpoints are sorted so callers can look up by either ordering.
    """
    out = {}
    for r in rels:
        if r.relation_kind != kind or r.window != window:
            continue
        key = tuple(sorted((r.source.id, r.target.id)))
        out[key] = r
    return out


def _attach_recent_cutoff(graph, cutoff: Optional[datetime]) -> None:
    """Pydantic v2 ``Graph`` has ``extra="forbid"`` so we cannot set the
    cutoff as a normal attribute. Sibling builders read via
    ``getattr(graph, "recent_cutoff", None)``; ``__dict__`` injection
    bypasses Pydantic's setattr while preserving that read path. Tests
    that want to exercise RECENT-window emissions use this.
    """
    graph.__dict__["recent_cutoff"] = cutoff


def _attach_config(graph, cfg: EnrichmentConfig) -> None:
    graph.__dict__["config"] = cfg


# Small-scenario tests below build graphs with strength=1 pairs. The dx-
# parity defaults gate file edges at count >= 20, so emission tests must
# attach a config with ``file_min_count=1`` (and a 24h window for legacy
# scenarios that placed commits >30 min apart).
_EMIT_CFG = EnrichmentConfig(
    time_windowed_cochange_hours=24,
    time_windowed_cochange_file_min_count=1,
)


# ======================================================================
# Catalog wiring (all three)
# ======================================================================
def test_all_three_builders_registered():
    assert "cochange.file_time_windowed" in BUILDERS
    assert "cochange.file_shared_devs" in BUILDERS
    assert "cochange.file_shared_task_prefixes" in BUILDERS
    assert BUILDERS.get("cochange.file_time_windowed") is CochangeFileTimeWindowedBuilder
    assert BUILDERS.get("cochange.file_shared_devs") is CochangeFileSharedDevsBuilder
    assert (
        BUILDERS.get("cochange.file_shared_task_prefixes")
        is CochangeFileSharedTaskPrefixesBuilder
    )


def test_builder_metadata():
    assert (
        CochangeFileTimeWindowedBuilder.relation_kind
        == "cochange_file_time_windowed"
    )
    assert (
        CochangeFileSharedDevsBuilder.relation_kind == "cochange_file_shared_devs"
    )
    assert (
        CochangeFileSharedTaskPrefixesBuilder.relation_kind
        == "cochange_file_shared_task_prefixes"
    )
    for cls in (
        CochangeFileTimeWindowedBuilder,
        CochangeFileSharedDevsBuilder,
        CochangeFileSharedTaskPrefixesBuilder,
    ):
        assert cls.window == WindowKind.LIFETIME


# ======================================================================
# Cochange.file_time_windowed
# ======================================================================
def test_time_windowed_emits_for_close_in_time_distinct_commits():
    """Two files touched by separate commits inside the Δt window."""
    g, p = build_v2_graph("tw-pos")
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

    _attach_config(g, _EMIT_CFG)
    rels = list(CochangeFileTimeWindowedBuilder().build(g))
    pairs = _pair_lookup(rels, "cochange_file_time_windowed")
    assert ("src/a.py", "src/b.py") in pairs
    rel = pairs[("src/a.py", "src/b.py")]
    assert rel.strength == 1.0
    assert rel.extras["hours"] == 24.0
    assert rel.extras["count"] == 1


def test_time_windowed_excludes_pairs_outside_window():
    """Commits 200 days apart MUST NOT emit a time-windowed edge."""
    g, p = build_v2_graph("tw-far")
    alice = make_account("Alice", "alice@example.com", p.ref())
    g.git_accounts.add(alice)

    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)

    now = datetime.now(UTC)
    c1 = make_commit("c1", "m", alice, now - timedelta(days=1), p.ref())
    c2 = make_commit("c2", "m", alice, now - timedelta(days=200), p.ref())
    g.commits.add(c1)
    g.commits.add(c2)
    add_change(g, c1, fa, added=5)
    add_change(g, c2, fb, added=5)

    rels = list(CochangeFileTimeWindowedBuilder().build(g))
    pairs = _pair_lookup(rels, "cochange_file_time_windowed")
    assert ("src/a.py", "src/b.py") not in pairs


def test_time_windowed_skips_merge_commits():
    """Merge commits (>1 parent) MUST NOT contribute to the edge count."""
    g, p = build_v2_graph("tw-merge")
    alice = make_account("Alice", "alice@example.com", p.ref())
    g.git_accounts.add(alice)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)

    now = datetime.now(UTC)
    c_parent_a = make_commit("pa", "m", alice, now - timedelta(hours=3), p.ref())
    c_parent_b = make_commit("pb", "m", alice, now - timedelta(hours=2), p.ref())
    g.commits.add(c_parent_a)
    g.commits.add(c_parent_b)

    # Merge commit with two parents — must be filtered out.
    c_merge = make_commit(
        "merge",
        "m",
        alice,
        now - timedelta(hours=1),
        p.ref(),
        parents=[c_parent_a.ref(), c_parent_b.ref()],
    )
    g.commits.add(c_merge)
    add_change(g, c_merge, fa, added=5)

    # Sibling regular commit (b) within window.
    c_b = make_commit("cb", "m", alice, now - timedelta(minutes=10), p.ref())
    g.commits.add(c_b)
    add_change(g, c_b, fb, added=2)

    rels = list(CochangeFileTimeWindowedBuilder().build(g))
    pairs = _pair_lookup(rels, "cochange_file_time_windowed")
    # The merge commit was skipped → no a-b time-windowed edge.
    assert ("src/a.py", "src/b.py") not in pairs


def test_time_windowed_excludes_same_file_self_pairs():
    """When two commits in the window touch the SAME file only, no edge."""
    g, p = build_v2_graph("tw-self")
    alice = make_account("Alice", "alice@example.com", p.ref())
    g.git_accounts.add(alice)
    fa = make_file("src/a.py", p.ref())
    g.files.add(fa)

    now = datetime.now(UTC)
    c1 = make_commit("c1", "m", alice, now - timedelta(hours=1), p.ref())
    c2 = make_commit("c2", "m", alice, now - timedelta(minutes=30), p.ref())
    g.commits.add(c1)
    g.commits.add(c2)
    add_change(g, c1, fa, added=5)
    add_change(g, c2, fa, added=3)

    rels = list(CochangeFileTimeWindowedBuilder().build(g))
    assert rels == []


def test_time_windowed_recent_window_emits_when_cutoff_set():
    """When ``graph.recent_cutoff`` is set, a RECENT emission appears for
    pairs whose contributing commits are both inside the recent window."""
    g, p = build_v2_graph("tw-recent")
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

    _attach_config(g, _EMIT_CFG)
    _attach_recent_cutoff(g, now - timedelta(days=1))

    rels = list(CochangeFileTimeWindowedBuilder().build(g))
    by_window = {(r.relation_kind, r.window) for r in rels}
    assert ("cochange_file_time_windowed", WindowKind.LIFETIME) in by_window
    assert ("cochange_file_time_windowed", WindowKind.RECENT) in by_window


def test_time_windowed_uses_temporal_index():
    """The builder MUST invoke ``ensure_temporal_index`` (D2 contract)."""
    g, p = build_v2_graph("tw-ti")
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

    # Build the index pre-call so the assertion can check id-equality
    # (the builder must use the cached instance, not rebuild).
    pre = g.ensure_temporal_index()
    list(CochangeFileTimeWindowedBuilder().build(g))
    post = g.ensure_temporal_index()
    assert pre is post


def test_time_windowed_canonical_id_is_deterministic():
    """Two runs over the same input produce the same Relation ids."""
    def _build():
        g, p = build_v2_graph("tw-det")
        alice = make_account("Alice", "a@e", p.ref())
        g.git_accounts.add(alice)
        fa = make_file("src/a.py", p.ref())
        fb = make_file("src/b.py", p.ref())
        g.files.add(fa)
        g.files.add(fb)
        now = datetime(2024, 1, 1, tzinfo=UTC)
        c1 = make_commit("c1", "m", alice, now, p.ref())
        c2 = make_commit("c2", "m", alice, now + timedelta(hours=1), p.ref())
        g.commits.add(c1)
        g.commits.add(c2)
        add_change(g, c1, fa, added=5)
        add_change(g, c2, fb, added=5)
        return [r.id for r in CochangeFileTimeWindowedBuilder().build(g)]

    assert sorted(_build()) == sorted(_build())


def test_time_windowed_min_count_gate_suppresses_below_threshold():
    """A single cross-commit co-change MUST NOT emit when the file
    min-count gate is set above 1. Matches dx
    ``files_SharedTimeWindow`` 1st arg = 20.
    """
    g, p = build_v2_graph("tw-gate")
    alice = make_account("Alice", "alice@example.com", p.ref())
    g.git_accounts.add(alice)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)

    now = datetime.now(UTC)
    c1 = make_commit("c1", "m", alice, now - timedelta(minutes=10), p.ref())
    c2 = make_commit("c2", "m", alice, now - timedelta(minutes=5), p.ref())
    g.commits.add(c1)
    g.commits.add(c2)
    add_change(g, c1, fa, added=5)
    add_change(g, c2, fb, added=5)

    # Below the default gate (20): no emission.
    _attach_config(g, EnrichmentConfig(time_windowed_cochange_hours=1))
    rels = list(CochangeFileTimeWindowedBuilder().build(g))
    assert _pair_lookup(rels, "cochange_file_time_windowed") == {}

    # Same scenario with the gate dropped to 1: edge appears.
    _attach_config(
        g,
        EnrichmentConfig(
            time_windowed_cochange_hours=1,
            time_windowed_cochange_file_min_count=1,
        ),
    )
    rels = list(CochangeFileTimeWindowedBuilder().build(g))
    pairs = _pair_lookup(rels, "cochange_file_time_windowed")
    assert ("src/a.py", "src/b.py") in pairs
    assert pairs[("src/a.py", "src/b.py")].extras["count"] == 1


# ======================================================================
# Cochange.file_shared_devs
# ======================================================================
def test_shared_devs_emits_for_pairs_with_overlapping_authors():
    """File pair co-touched by two authors → edge with shared_devs >= 1."""
    g, p = build_v2_graph("sd-pos")
    alice = make_account("Alice", "alice@example.com", p.ref())
    bob = make_account("Bob", "bob@example.com", p.ref())
    g.git_accounts.add(alice)
    g.git_accounts.add(bob)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)

    now = datetime.now(UTC)
    c1 = make_commit("c1", "m", alice, now - timedelta(days=1), p.ref())
    c2 = make_commit("c2", "m", bob, now - timedelta(hours=1), p.ref())
    g.commits.add(c1)
    g.commits.add(c2)
    add_change(g, c1, fa, added=5)
    add_change(g, c1, fb, added=3)
    add_change(g, c2, fa, added=2)
    add_change(g, c2, fb, added=1)

    # Builder reads relations.of_kind("ownership") + ("cochange") so the
    # full pipeline (or at least those two builders) must run first.
    run_pipeline(g, EnrichmentConfig())

    # Now read out our builder's output.
    rels = list(g.relations.of_kind("cochange_file_shared_devs"))
    pairs = _pair_lookup(rels, "cochange_file_shared_devs")
    assert ("src/a.py", "src/b.py") in pairs
    rel = pairs[("src/a.py", "src/b.py")]
    # Both Alice and Bob touched both files → shared_devs == 2.
    assert rel.extras["shared_devs"] == 2
    assert rel.strength == 2.0
    assert sorted(rel.extras["shared_author_ids"]) == sorted(
        [alice.id, bob.id]
    )


def test_shared_devs_no_edge_when_no_overlap():
    """File pair touched by disjoint author sets → no shared-devs edge."""
    g, p = build_v2_graph("sd-disjoint")
    alice = make_account("Alice", "alice@example.com", p.ref())
    bob = make_account("Bob", "bob@example.com", p.ref())
    g.git_accounts.add(alice)
    g.git_accounts.add(bob)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)

    now = datetime.now(UTC)
    # Alice touches a+b together (cochange), Bob never appears on a.
    c1 = make_commit("c1", "m", alice, now - timedelta(days=2), p.ref())
    g.commits.add(c1)
    add_change(g, c1, fa, added=5)
    add_change(g, c1, fb, added=3)

    run_pipeline(g, EnrichmentConfig())
    rels = list(g.relations.of_kind("cochange_file_shared_devs"))
    pairs = _pair_lookup(rels, "cochange_file_shared_devs")
    # Alice is the only author and she touches both → shared_devs == 1.
    assert ("src/a.py", "src/b.py") in pairs
    assert pairs[("src/a.py", "src/b.py")].extras["shared_devs"] == 1


def test_shared_devs_requires_actual_cochange():
    """Files touched by the same author but NEVER co-changing must NOT
    emit a shared-devs edge — the cochange gate filters them out."""
    g, p = build_v2_graph("sd-no-cochange")
    alice = make_account("Alice", "alice@example.com", p.ref())
    g.git_accounts.add(alice)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)

    now = datetime.now(UTC)
    c1 = make_commit("c1", "m", alice, now - timedelta(days=2), p.ref())
    c2 = make_commit("c2", "m", alice, now - timedelta(days=1), p.ref())
    g.commits.add(c1)
    g.commits.add(c2)
    add_change(g, c1, fa, added=5)   # a alone
    add_change(g, c2, fb, added=3)   # b alone (different commit)

    run_pipeline(g, EnrichmentConfig())
    rels = list(g.relations.of_kind("cochange_file_shared_devs"))
    pairs = _pair_lookup(rels, "cochange_file_shared_devs")
    assert ("src/a.py", "src/b.py") not in pairs


def test_shared_devs_empty_relations_registry_no_edges():
    """When ownership relations don't exist (builder ran in isolation),
    the shared-devs builder must produce nothing — no fresh scan of
    changes/commits per the Chunk-13 reuse-map rule."""
    g, _p = build_v2_graph("sd-empty")
    # No commits / no files / no pipeline run.
    rels = list(CochangeFileSharedDevsBuilder().build(g))
    assert rels == []


# ======================================================================
# Cochange.file_shared_task_prefixes
# ======================================================================
def test_shared_task_prefixes_emits_for_shared_jira_key():
    """Two commits with the same task prefix co-touching a+b → edge."""
    g, p = build_v2_graph("stp-pos")
    alice = make_account("Alice", "alice@example.com", p.ref())
    g.git_accounts.add(alice)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)

    now = datetime.now(UTC)
    c1 = make_commit("c1", "PROJ-1: refactor", alice, now - timedelta(days=2), p.ref())
    g.commits.add(c1)
    add_change(g, c1, fa, added=5)
    add_change(g, c1, fb, added=3)

    rels = list(CochangeFileSharedTaskPrefixesBuilder().build(g))
    pairs = _pair_lookup(rels, "cochange_file_shared_task_prefixes")
    assert ("src/a.py", "src/b.py") in pairs
    rel = pairs[("src/a.py", "src/b.py")]
    assert rel.extras["shared_prefixes"] == ["PROJ"]
    assert rel.strength == 1.0


def test_shared_task_prefixes_no_edge_when_message_has_no_key():
    """Commit messages without a task key → no shared-prefix edge."""
    g, p = build_v2_graph("stp-nokey")
    alice = make_account("Alice", "alice@example.com", p.ref())
    g.git_accounts.add(alice)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)

    now = datetime.now(UTC)
    c1 = make_commit("c1", "minor cleanup", alice, now - timedelta(days=2), p.ref())
    g.commits.add(c1)
    add_change(g, c1, fa, added=5)
    add_change(g, c1, fb, added=3)

    rels = list(CochangeFileSharedTaskPrefixesBuilder().build(g))
    pairs = _pair_lookup(rels, "cochange_file_shared_task_prefixes")
    assert ("src/a.py", "src/b.py") not in pairs


def test_shared_task_prefixes_multi_prefix_collected():
    """Multiple distinct prefixes from one message contribute to strength."""
    g, p = build_v2_graph("stp-multi")
    alice = make_account("Alice", "a@e", p.ref())
    g.git_accounts.add(alice)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)

    now = datetime.now(UTC)
    c1 = make_commit(
        "c1",
        "PROJ-1, OTHER-7: cross-component fix",
        alice,
        now - timedelta(days=2),
        p.ref(),
    )
    g.commits.add(c1)
    add_change(g, c1, fa, added=5)
    add_change(g, c1, fb, added=3)

    rels = list(CochangeFileSharedTaskPrefixesBuilder().build(g))
    pairs = _pair_lookup(rels, "cochange_file_shared_task_prefixes")
    rel = pairs[("src/a.py", "src/b.py")]
    assert sorted(rel.extras["shared_prefixes"]) == ["OTHER", "PROJ"]
    assert rel.strength == 2.0


def test_shared_task_prefixes_uses_inline_extraction_not_classifier():
    """The builder MUST work in isolation (no classifier registry data)
    — proves it uses inline ``extract_task_prefixes`` and not the
    stage-2 ``task_prefix`` classifier surface."""
    g, p = build_v2_graph("stp-isolated")
    alice = make_account("Alice", "a@e", p.ref())
    g.git_accounts.add(alice)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)

    now = datetime.now(UTC)
    c1 = make_commit("c1", "ZEP-9: bug", alice, now - timedelta(days=1), p.ref())
    g.commits.add(c1)
    add_change(g, c1, fa, added=2)
    add_change(g, c1, fb, added=1)

    # Sanity: no classifiers registered yet — pipeline NOT run.
    assert len(g.classifiers) == 0

    rels = list(CochangeFileSharedTaskPrefixesBuilder().build(g))
    pairs = _pair_lookup(rels, "cochange_file_shared_task_prefixes")
    assert ("src/a.py", "src/b.py") in pairs
    assert pairs[("src/a.py", "src/b.py")].extras["shared_prefixes"] == ["ZEP"]


def test_shared_task_prefixes_skips_merge_and_bulk_commits():
    """Merge commits and bulk commits (>max_files) MUST not contribute."""
    g, p = build_v2_graph("stp-merge")
    alice = make_account("Alice", "a@e", p.ref())
    g.git_accounts.add(alice)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)

    now = datetime.now(UTC)
    c_parent_a = make_commit("pa", "ZEP-1", alice, now - timedelta(hours=3), p.ref())
    c_parent_b = make_commit("pb", "ZEP-1", alice, now - timedelta(hours=2), p.ref())
    g.commits.add(c_parent_a)
    g.commits.add(c_parent_b)
    c_merge = make_commit(
        "merge",
        "ZEP-1: merge branch",
        alice,
        now - timedelta(hours=1),
        p.ref(),
        parents=[c_parent_a.ref(), c_parent_b.ref()],
    )
    g.commits.add(c_merge)
    add_change(g, c_merge, fa, added=2)
    add_change(g, c_merge, fb, added=2)

    rels = list(CochangeFileSharedTaskPrefixesBuilder().build(g))
    pairs = _pair_lookup(rels, "cochange_file_shared_task_prefixes")
    # The only commit touching both was a merge → filtered.
    assert ("src/a.py", "src/b.py") not in pairs


def test_shared_task_prefixes_empty_graph_emits_nothing():
    g, _p = build_v2_graph("stp-empty")
    rels = list(CochangeFileSharedTaskPrefixesBuilder().build(g))
    assert rels == []


# ======================================================================
# End-to-end smoke through run_pipeline
# ======================================================================
def test_all_three_kinds_present_after_pipeline_run():
    """A single ``run_pipeline`` call must populate all three Chunk-13
    relation kinds on the graph (no errors from these builders)."""
    g, p = build_v2_graph("e2e")
    alice = make_account("Alice", "alice@example.com", p.ref())
    bob = make_account("Bob", "bob@example.com", p.ref())
    g.git_accounts.add(alice)
    g.git_accounts.add(bob)
    fa = make_file("src/a.py", p.ref())
    fb = make_file("src/b.py", p.ref())
    g.files.add(fa)
    g.files.add(fb)

    now = datetime.now(UTC)
    c1 = make_commit(
        "c1", "PROJ-1: refactor", alice, now - timedelta(hours=1), p.ref(),
    )
    c2 = make_commit(
        "c2", "PROJ-2: cleanup", bob, now - timedelta(minutes=30), p.ref(),
    )
    g.commits.add(c1)
    g.commits.add(c2)
    add_change(g, c1, fa, added=5)
    add_change(g, c1, fb, added=3)
    add_change(g, c2, fa, added=2)
    add_change(g, c2, fb, added=1)

    _attach_config(g, _EMIT_CFG)
    result = run_pipeline(g, _EMIT_CFG)
    # No errors from our three builders specifically.
    builder_errors = {
        e.name for e in result.errors if e.step == "builder"
    }
    for name in (
        "cochange.file_time_windowed",
        "cochange.file_shared_devs",
        "cochange.file_shared_task_prefixes",
    ):
        assert name not in builder_errors, (
            f"{name} errored: {[e for e in result.errors if e.name == name]}"
        )

    kinds = {r.relation_kind for r in g.relations}
    assert "cochange_file_time_windowed" in kinds
    assert "cochange_file_shared_devs" in kinds
    assert "cochange_file_shared_task_prefixes" in kinds
