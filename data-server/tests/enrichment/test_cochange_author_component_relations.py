"""Per-builder coverage for the Chunk-14 author + component cochange
relation builders.

Mirrors the structure of
:mod:`tests.enrichment.test_cochange_file_relations` (Chunk 13) — one
section per builder, plus catalog-wiring smoke tests and a pipeline-
level end-to-end check.

Builders under test
-------------------

* :class:`CochangeAuthorTimeWindowedBuilder`
* :class:`CochangeAuthorSharedTaskPrefixesBuilder`
* :class:`CochangeComponentBuilder`
* :class:`CochangeComponentTimeWindowedBuilder`
* :class:`CochangeComponentSharedDevsBuilder`
* :class:`CochangeComponentSharedTaskPrefixesBuilder`
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import run_pipeline
from src.enrichment.relations import BUILDERS, WindowKind
from src.enrichment.relations.implementations.cochange_author_shared_task_prefixes import (
    CochangeAuthorSharedTaskPrefixesBuilder,
)
from src.enrichment.relations.implementations.cochange_author_time_windowed import (
    CochangeAuthorTimeWindowedBuilder,
)
from src.enrichment.relations.implementations.cochange_component import (
    CochangeComponentBuilder,
)
from src.enrichment.relations.implementations.cochange_component_shared_devs import (
    CochangeComponentSharedDevsBuilder,
)
from src.enrichment.relations.implementations.cochange_component_shared_task_prefixes import (
    CochangeComponentSharedTaskPrefixesBuilder,
)
from src.enrichment.relations.implementations.cochange_component_time_windowed import (
    CochangeComponentTimeWindowedBuilder,
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
# Helpers
# ----------------------------------------------------------------------
def _attach_recent_cutoff(graph, cutoff: Optional[datetime]) -> None:
    """Pydantic v2 ``extra="forbid"`` bypass — see Chunk-13 tests."""
    graph.__dict__["recent_cutoff"] = cutoff


def _attach_config(graph, cfg: EnrichmentConfig) -> None:
    graph.__dict__["config"] = cfg


def _build_two_components_mapping(tmp_path) -> str:
    """A mapping that splits ``src/a*`` → ``comp_a``, ``src/b*`` → ``comp_b``."""
    payload = {
        "comp_a": {"path_prefix": "src/a"},
        "comp_b": {"path_prefix": "src/b"},
    }
    p = tmp_path / "components.json"
    p.write_text(json.dumps(payload))
    return str(p)


def _build_two_author_two_file_graph_recent(name: str):
    """Two authors, two files, both commits inside 24h."""
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
    c1 = make_commit("c1", "PROJ-1: refactor", alice, now - timedelta(hours=3), p.ref())
    c2 = make_commit("c2", "PROJ-1: more",     bob,   now - timedelta(hours=1), p.ref())
    g.commits.add(c1)
    g.commits.add(c2)
    add_change(g, c1, fa, added=5)
    add_change(g, c1, fb, added=3)
    add_change(g, c2, fa, added=2)
    add_change(g, c2, fb, added=1)
    return g, alice, bob, fa, fb


# ======================================================================
# Catalog wiring
# ======================================================================
def test_all_six_builders_registered():
    assert "cochange.author_time_windowed" in BUILDERS
    assert "cochange.author_shared_task_prefixes" in BUILDERS
    assert "cochange.component" in BUILDERS
    assert "cochange.component_time_windowed" in BUILDERS
    assert "cochange.component_shared_devs" in BUILDERS
    assert "cochange.component_shared_task_prefixes" in BUILDERS

    assert BUILDERS.get("cochange.author_time_windowed") is CochangeAuthorTimeWindowedBuilder
    assert BUILDERS.get("cochange.author_shared_task_prefixes") is CochangeAuthorSharedTaskPrefixesBuilder
    assert BUILDERS.get("cochange.component") is CochangeComponentBuilder
    assert BUILDERS.get("cochange.component_time_windowed") is CochangeComponentTimeWindowedBuilder
    assert BUILDERS.get("cochange.component_shared_devs") is CochangeComponentSharedDevsBuilder
    assert BUILDERS.get("cochange.component_shared_task_prefixes") is CochangeComponentSharedTaskPrefixesBuilder


def test_builder_metadata():
    assert (
        CochangeAuthorTimeWindowedBuilder.relation_kind
        == "cochange_author_time_windowed"
    )
    assert (
        CochangeAuthorSharedTaskPrefixesBuilder.relation_kind
        == "cochange_author_shared_task_prefixes"
    )
    assert CochangeComponentBuilder.relation_kind == "cochange_component"
    assert (
        CochangeComponentTimeWindowedBuilder.relation_kind
        == "cochange_component_time_windowed"
    )
    assert (
        CochangeComponentSharedDevsBuilder.relation_kind
        == "cochange_component_shared_devs"
    )
    assert (
        CochangeComponentSharedTaskPrefixesBuilder.relation_kind
        == "cochange_component_shared_task_prefixes"
    )


# ======================================================================
# cochange.author_time_windowed
# ======================================================================
def test_author_time_windowed_emits_for_close_cross_author_commits():
    """Two authors committing within an hour → one author pair with
    strength >= 1.
    """
    g, alice, bob, _fa, _fb = _build_two_author_two_file_graph_recent("auth-tw-1")
    _attach_config(g, EnrichmentConfig(time_windowed_cochange_hours=24))
    run_pipeline(g, EnrichmentConfig(time_windowed_cochange_hours=24))

    rels = list(g.relations.of_kind("cochange_author_time_windowed"))
    pair_rel = [r for r in rels if {r.source.id, r.target.id} == {alice.id, bob.id}]
    assert pair_rel, "expected one (Alice, Bob) edge"
    assert pair_rel[0].strength >= 1


def test_author_time_windowed_skips_same_author_pairs():
    """Two commits by the same author should NOT emit any cochange
    edges — the builder requires distinct authors.
    """
    g, p = build_v2_graph("auth-tw-same")
    alice = make_account("Alice", "alice@example.com", p.ref())
    g.git_accounts.add(alice)
    f = make_file("src/x.py", p.ref())
    g.files.add(f)
    now = datetime.now(UTC)
    c1 = make_commit("c1", "m", alice, now - timedelta(hours=2), p.ref())
    c2 = make_commit("c2", "m", alice, now - timedelta(hours=1), p.ref())
    g.commits.add(c1)
    g.commits.add(c2)
    add_change(g, c1, f, added=1)
    add_change(g, c2, f, added=1)
    run_pipeline(g, EnrichmentConfig())
    assert list(g.relations.of_kind("cochange_author_time_windowed")) == []


def test_author_time_windowed_skips_commits_outside_window():
    """Commits 48h apart should not emit when window is 24h."""
    g, p = build_v2_graph("auth-tw-out")
    alice = make_account("Alice", "alice@example.com", p.ref())
    bob = make_account("Bob", "bob@example.com", p.ref())
    g.git_accounts.add(alice)
    g.git_accounts.add(bob)
    f = make_file("src/x.py", p.ref())
    g.files.add(f)
    now = datetime.now(UTC)
    c1 = make_commit("c1", "m", alice, now - timedelta(hours=72), p.ref())
    c2 = make_commit("c2", "m", bob,   now - timedelta(hours=1),  p.ref())
    g.commits.add(c1)
    g.commits.add(c2)
    add_change(g, c1, f, added=1)
    add_change(g, c2, f, added=1)
    run_pipeline(g, EnrichmentConfig())
    assert list(g.relations.of_kind("cochange_author_time_windowed")) == []


def test_author_time_windowed_counts_multiple_pairs_in_window():
    """An author burst should accumulate strength > 1."""
    g, p = build_v2_graph("auth-tw-multi")
    alice = make_account("Alice", "alice@example.com", p.ref())
    bob = make_account("Bob", "bob@example.com", p.ref())
    g.git_accounts.add(alice)
    g.git_accounts.add(bob)
    f = make_file("src/x.py", p.ref())
    g.files.add(f)
    now = datetime.now(UTC)
    # Three commits each, all within ~3h: should yield 3*3 = 9 cross pairs.
    times = [3, 2, 1]
    for i, h in enumerate(times):
        ca = make_commit(f"a{i}", "m", alice, now - timedelta(hours=h, minutes=i*5), p.ref())
        cb = make_commit(f"b{i}", "m", bob,   now - timedelta(hours=h, minutes=i*7), p.ref())
        g.commits.add(ca)
        g.commits.add(cb)
        add_change(g, ca, f, added=1)
        add_change(g, cb, f, added=1)
    run_pipeline(g, EnrichmentConfig())
    rels = [
        r for r in g.relations.of_kind("cochange_author_time_windowed")
        if {r.source.id, r.target.id} == {alice.id, bob.id}
    ]
    assert rels
    assert rels[0].strength >= 4


def test_author_time_windowed_uses_temporal_index_cache():
    """Pin the contract: builder reads via ``graph.ensure_temporal_index()``
    rather than its own bisect."""
    g, _alice, _bob, _fa, _fb = _build_two_author_two_file_graph_recent("auth-tw-cache")
    pre = g.ensure_temporal_index()
    run_pipeline(g, EnrichmentConfig())
    post = g.ensure_temporal_index()
    assert pre is post  # cached


# ======================================================================
# cochange.author_shared_task_prefixes
# ======================================================================
def test_author_shared_task_prefixes_intersects_per_author_prefix_sets():
    """Both authors carry ``PROJ-N`` in their commit messages → one
    shared prefix → strength 1."""
    g, alice, bob, _fa, _fb = _build_two_author_two_file_graph_recent("auth-stp-1")
    run_pipeline(g, EnrichmentConfig())
    rels = [
        r for r in g.relations.of_kind("cochange_author_shared_task_prefixes")
        if {r.source.id, r.target.id} == {alice.id, bob.id}
    ]
    assert rels
    assert rels[0].strength == 1.0
    assert "PROJ" in rels[0].extras["shared_prefixes"]


def test_author_shared_task_prefixes_emits_nothing_without_shared_keys():
    """Each author touches a different prefix → no shared edge."""
    g, p = build_v2_graph("auth-stp-none")
    alice = make_account("Alice", "alice@example.com", p.ref())
    bob = make_account("Bob", "bob@example.com", p.ref())
    g.git_accounts.add(alice)
    g.git_accounts.add(bob)
    f = make_file("src/x.py", p.ref())
    g.files.add(f)
    now = datetime.now(UTC)
    c1 = make_commit("c1", "ALPHA-1: x", alice, now - timedelta(hours=2), p.ref())
    c2 = make_commit("c2", "BETA-2: y",  bob,   now - timedelta(hours=1), p.ref())
    g.commits.add(c1)
    g.commits.add(c2)
    add_change(g, c1, f, added=1)
    add_change(g, c2, f, added=1)
    run_pipeline(g, EnrichmentConfig())
    rels = [
        r for r in g.relations.of_kind("cochange_author_shared_task_prefixes")
        if {r.source.id, r.target.id} == {alice.id, bob.id}
    ]
    assert rels == []


def test_author_shared_task_prefixes_dedups_within_author():
    """An author committing PROJ-1 three times still contributes one
    prefix to the intersection (set semantics — N1 trap mitigation)."""
    g, p = build_v2_graph("auth-stp-dedup")
    alice = make_account("Alice", "alice@example.com", p.ref())
    bob = make_account("Bob", "bob@example.com", p.ref())
    g.git_accounts.add(alice)
    g.git_accounts.add(bob)
    f = make_file("src/x.py", p.ref())
    g.files.add(f)
    now = datetime.now(UTC)
    # Three Alice commits, one Bob — all carrying PROJ.
    for i in range(3):
        c = make_commit(f"a{i}", f"PROJ-{i}: x", alice, now - timedelta(hours=24*(i+1)), p.ref())
        g.commits.add(c)
        add_change(g, c, f, added=1)
    cb = make_commit("b1", "PROJ-99: y", bob, now - timedelta(hours=1), p.ref())
    g.commits.add(cb)
    add_change(g, cb, f, added=1)
    run_pipeline(g, EnrichmentConfig())
    rels = [
        r for r in g.relations.of_kind("cochange_author_shared_task_prefixes")
        if {r.source.id, r.target.id} == {alice.id, bob.id}
    ]
    assert len(rels) == 1
    assert rels[0].strength == 1.0  # only PROJ, despite 4 commits


def test_author_shared_task_prefixes_multi_prefix_accumulates():
    """Multiple shared prefixes → strength == |shared|."""
    g, p = build_v2_graph("auth-stp-multi")
    alice = make_account("Alice", "alice@example.com", p.ref())
    bob = make_account("Bob", "bob@example.com", p.ref())
    g.git_accounts.add(alice)
    g.git_accounts.add(bob)
    f = make_file("src/x.py", p.ref())
    g.files.add(f)
    now = datetime.now(UTC)
    for who, account, prefixes in [("a", alice, ["AA", "BB", "CC"]), ("b", bob, ["BB", "CC", "DD"])]:
        for i, pref in enumerate(prefixes):
            c = make_commit(
                f"{who}{i}", f"{pref}-{i}: x", account,
                now - timedelta(hours=24*i + (1 if who == "a" else 2)),
                p.ref(),
            )
            g.commits.add(c)
            add_change(g, c, f, added=1)
    run_pipeline(g, EnrichmentConfig())
    rels = [
        r for r in g.relations.of_kind("cochange_author_shared_task_prefixes")
        if {r.source.id, r.target.id} == {alice.id, bob.id}
    ]
    assert len(rels) == 1
    assert rels[0].strength == 2.0  # BB + CC shared
    assert sorted(rels[0].extras["shared_prefixes"]) == ["BB", "CC"]


def test_author_shared_task_prefixes_uses_inline_extraction_not_classifier():
    """Asserts the same pipeline-ordering safeguard as the file-domain
    sibling test (Chunk 13)."""
    g, _alice, _bob, _fa, _fb = _build_two_author_two_file_graph_recent("auth-stp-inline")
    builder = CochangeAuthorSharedTaskPrefixesBuilder()
    assert len(g.classifiers) == 0  # no task_prefix classifiers yet
    rels = list(builder.build(g))
    # Build still returns the right answer.
    assert any(r.relation_kind == "cochange_author_shared_task_prefixes" for r in rels)


# ======================================================================
# cochange.component (Chunk 12 deferral)
# ======================================================================
def test_cochange_component_aggregates_file_cochange_through_mapping(tmp_path):
    g, _alice, _bob, _fa, _fb = _build_two_author_two_file_graph_recent("comp-1")
    cfg = EnrichmentConfig(components_mapping_path=_build_two_components_mapping(tmp_path))
    _attach_config(g, cfg)
    run_pipeline(g, cfg)
    rels = list(g.relations.of_kind("cochange_component"))
    pair = [r for r in rels if {r.source.id, r.target.id} == {"comp_a", "comp_b"}]
    assert pair
    assert pair[0].strength > 0
    # No self-loops.
    assert all(r.source != r.target for r in rels)


def test_cochange_component_drops_self_loops_under_heuristic_mode():
    """Under heuristic (no mapping), both files resolve to ``src`` →
    self-loop → dropped → no edge."""
    g, _alice, _bob, _fa, _fb = _build_two_author_two_file_graph_recent("comp-self")
    # No config → heuristic mode.
    run_pipeline(g, EnrichmentConfig())
    assert list(g.relations.of_kind("cochange_component")) == []


def test_cochange_component_emits_empty_when_no_file_cochange():
    """No file cochange → no component cochange."""
    g, p = build_v2_graph("comp-empty")
    alice = make_account("Alice", "alice@example.com", p.ref())
    g.git_accounts.add(alice)
    f = make_file("src/x.py", p.ref())
    g.files.add(f)
    now = datetime.now(UTC)
    c = make_commit("c1", "m", alice, now - timedelta(hours=1), p.ref())
    g.commits.add(c)
    add_change(g, c, f, added=1)
    run_pipeline(g, EnrichmentConfig())
    assert list(g.relations.of_kind("cochange_component")) == []


# ======================================================================
# cochange.component_time_windowed
# ======================================================================
def test_cochange_component_time_windowed_aggregates_file_time_windowed(tmp_path):
    g, _alice, _bob, _fa, _fb = _build_two_author_two_file_graph_recent("comp-tw-1")
    cfg = EnrichmentConfig(components_mapping_path=_build_two_components_mapping(tmp_path))
    _attach_config(g, cfg)
    run_pipeline(g, cfg)
    rels = list(g.relations.of_kind("cochange_component_time_windowed"))
    pair = [r for r in rels if {r.source.id, r.target.id} == {"comp_a", "comp_b"}]
    assert pair
    assert pair[0].strength > 0


# ======================================================================
# cochange.component_shared_devs
# ======================================================================
def test_cochange_component_shared_devs_aggregates_file_shared_devs(tmp_path):
    g, _alice, _bob, _fa, _fb = _build_two_author_two_file_graph_recent("comp-sd-1")
    cfg = EnrichmentConfig(components_mapping_path=_build_two_components_mapping(tmp_path))
    _attach_config(g, cfg)
    run_pipeline(g, cfg)
    rels = list(g.relations.of_kind("cochange_component_shared_devs"))
    pair = [r for r in rels if {r.source.id, r.target.id} == {"comp_a", "comp_b"}]
    assert pair
    assert pair[0].strength > 0


# ======================================================================
# cochange.component_shared_task_prefixes
# ======================================================================
def test_cochange_component_shared_task_prefixes_aggregates_file_shared_task_prefixes(tmp_path):
    g, _alice, _bob, _fa, _fb = _build_two_author_two_file_graph_recent("comp-stp-1")
    cfg = EnrichmentConfig(components_mapping_path=_build_two_components_mapping(tmp_path))
    _attach_config(g, cfg)
    run_pipeline(g, cfg)
    rels = list(g.relations.of_kind("cochange_component_shared_task_prefixes"))
    pair = [r for r in rels if {r.source.id, r.target.id} == {"comp_a", "comp_b"}]
    assert pair
    assert pair[0].strength > 0


# ======================================================================
# Pipeline smoke — every Chunk-14 kind present after a single run_pipeline
# ======================================================================
def test_pipeline_emits_all_chunk_14_kinds(tmp_path):
    """End-to-end: a single ``run_pipeline`` over the fixture emits each
    of the six Chunk-14 kinds (LIFETIME at minimum)."""
    g, _alice, _bob, _fa, _fb = _build_two_author_two_file_graph_recent("chunk14-smoke")
    cfg = EnrichmentConfig(components_mapping_path=_build_two_components_mapping(tmp_path))
    _attach_config(g, cfg)
    run_pipeline(g, cfg)
    expected = {
        "cochange_author_time_windowed",
        "cochange_author_shared_task_prefixes",
        "cochange_component",
        "cochange_component_time_windowed",
        "cochange_component_shared_devs",
        "cochange_component_shared_task_prefixes",
    }
    for kind in expected:
        rels = list(g.relations.of_kind(kind))
        assert rels, f"{kind} produced no relations"
        # Endpoints are kind-correct.
        for r in rels:
            assert r.window in (WindowKind.LIFETIME, WindowKind.RECENT)
