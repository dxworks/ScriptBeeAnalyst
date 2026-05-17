"""Chunk-16 anomaly traits — knowledge family extras + OrphanCausers.

The :file:`test_a21_file_traits.py` restored regression file covers six
of the ten ``anomaly.knowledge.*`` traits (Accumulator, Polarised,
OwnerChurn, Solitaire, TeamChurn, WeakOwnership). This file covers the
remaining four:

* ``anomaly.knowledge.Orphan``         — single-author file, last touch
                                          older than the recent window.
* ``anomaly.knowledge.BusFactor1``     — dominant-author file with the
                                          PolarisedOwnership suppression
                                          asserted.
* ``anomaly.knowledge.SharedKnowledge`` — high-entropy file with many
                                          balanced authors.
* ``anomaly.knowledge.OrphanCausers``  — retired authors whose
                                          orphan-flagged files trigger
                                          the author-level trait.
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
    return graph.traits.for_target(EntityRef(kind=EntityKind.FILE, id=file_id))


def _traits_for_account(graph, account_id: str) -> tuple:
    return graph.traits.for_target(
        EntityRef(kind=EntityKind.GIT_ACCOUNT, id=account_id)
    )


def _names(traits) -> set[str]:
    return {t.name for t in traits}


def _trait(traits, name: str):
    return next((t for t in traits if t.name == name), None)


def _run(graph) -> None:
    run_pipeline(graph, EnrichmentConfig())


# ----------------------------------------------------------------------
# Registry wiring
# ----------------------------------------------------------------------
def test_anomaly_knowledge_metric_is_registered():
    assert any(m.name == "anomaly.knowledge" for m in METRICS.all())


def test_anomaly_knowledge_emits_ten_named_traits():
    cls = next(m for m in METRICS.all() if m.name == "anomaly.knowledge")
    expected = {
        "anomaly.knowledge.Orphan",
        "anomaly.knowledge.BusFactor1",
        "anomaly.knowledge.SharedKnowledge",
        "anomaly.knowledge.Accumulator",
        "anomaly.knowledge.OwnerChurn",
        "anomaly.knowledge.PolarisedOwnership",
        "anomaly.knowledge.Solitaire",
        "anomaly.knowledge.TeamChurn",
        "anomaly.knowledge.WeakOwnership",
        "anomaly.knowledge.OrphanCausers",
    }
    assert expected == set(cls.outputs.emits_traits)


# ----------------------------------------------------------------------
# Orphan
# ----------------------------------------------------------------------
def test_orphan_fires_for_single_author_with_stale_last_touch():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("orphan_pos")
    # Pin the recent cutoff (test-stub convention — see Chunk-11 handoff
    # §"Open issues" #1) so the "last < cutoff" check is anchored at the
    # caller's intent, not at the data's own latest-commit floor.
    graph.__dict__["recent_cutoff"] = now - timedelta(days=90)
    alice = make_account("Alice", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/orphan.py", project.ref())
    graph.files.add(f)
    # 3 commits, all by Alice, all >180 days ago. Recent window default 90 d.
    for i in range(3):
        c = make_commit(
            f"o_{i}", "feat", alice,
            now - timedelta(days=200 + i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, f, added=20)
    _run(graph)
    traits = _traits_for_file(graph, "src/orphan.py")
    t = _trait(traits, "anomaly.knowledge.Orphan")
    assert t is not None
    assert t.evidence["author"] == alice.id
    assert int(t.evidence["churn"]) >= 1


def test_orphan_suppressed_when_only_author_still_active():
    """Single-author file with recent commits → not Orphan."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("orphan_neg")
    alice = make_account("Alice", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/active.py", project.ref())
    graph.files.add(f)
    for i in range(3):
        c = make_commit(
            f"a_{i}", "feat", alice,
            now - timedelta(days=5 + i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, f, added=10)
    _run(graph)
    traits = _traits_for_file(graph, "src/active.py")
    assert "anomaly.knowledge.Orphan" not in _names(traits)


# ----------------------------------------------------------------------
# BusFactor1 + mutual exclusion with PolarisedOwnership
# ----------------------------------------------------------------------
def test_busfactor1_fires_on_dominant_author_and_suppresses_polarised():
    """Alice has 80%+ of churn → BusFactor1 fires, PolarisedOwnership does NOT."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("bf1")
    alice = make_account("Alice", "a@x", project.ref())
    bob = make_account("Bob", "b@x", project.ref())
    for a in (alice, bob):
        graph.git_accounts.add(a)
    f = make_file("src/bf.py", project.ref())
    graph.files.add(f)
    # Alice 9 × 100 lines; Bob 1 × 20 lines → Alice ~98% dominance.
    for i in range(9):
        c = make_commit(f"a_{i}", "feat", alice,
                        now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=100)
    c = make_commit("b_1", "feat", bob, now - timedelta(days=5), project.ref())
    graph.commits.add(c)
    add_change(graph, c, f, added=20)

    _run(graph)
    traits = _traits_for_file(graph, "src/bf.py")
    names = _names(traits)
    assert "anomaly.knowledge.BusFactor1" in names
    # Per legacy v1 + module docstring: BusFactor1 suppresses Polarised
    # on the same file (the "tight pair" vs "single dominant" overlap).
    assert "anomaly.knowledge.PolarisedOwnership" not in names

    bf1 = _trait(traits, "anomaly.knowledge.BusFactor1")
    assert bf1.evidence["dominant_author"] == alice.id
    assert bf1.evidence["dominance_ratio"] >= 0.80
    assert bf1.evidence["distinct_authors"] == 2


def test_busfactor1_does_not_fire_below_dominance_threshold():
    """Two authors at 60/40 — below 80% threshold."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("bf1_neg")
    alice = make_account("Alice", "a@x", project.ref())
    bob = make_account("Bob", "b@x", project.ref())
    for a in (alice, bob):
        graph.git_accounts.add(a)
    f = make_file("src/balance.py", project.ref())
    graph.files.add(f)
    for i in range(3):
        c = make_commit(f"a_{i}", "feat", alice,
                        now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=60)
    for i in range(2):
        c = make_commit(f"b_{i}", "feat", bob,
                        now - timedelta(days=15 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=60)
    _run(graph)
    traits = _traits_for_file(graph, "src/balance.py")
    assert "anomaly.knowledge.BusFactor1" not in _names(traits)


# ----------------------------------------------------------------------
# SharedKnowledge
# ----------------------------------------------------------------------
def test_shared_knowledge_fires_on_high_entropy_balanced_authors():
    """5 authors at roughly equal churn → entropy ≥ 1.5 nats."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("shared")
    authors = []
    for i in range(5):
        a = make_account(f"A{i}", f"a{i}@x", project.ref())
        graph.git_accounts.add(a)
        authors.append(a)
    f = make_file("src/shared.py", project.ref())
    graph.files.add(f)
    for i, a in enumerate(authors):
        c = make_commit(
            f"c_{i}", "feat", a,
            now - timedelta(days=10 + i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, f, added=20)
    _run(graph)
    traits = _traits_for_file(graph, "src/shared.py")
    t = _trait(traits, "anomaly.knowledge.SharedKnowledge")
    assert t is not None
    assert t.evidence["entropy"] >= 1.5
    assert t.evidence["distinct_authors"] == 5


# ----------------------------------------------------------------------
# OrphanCausers
# ----------------------------------------------------------------------
def test_orphan_causers_fires_for_retired_author_of_many_orphan_files():
    """Bob authored ≥3 files that are now Orphan AND has ≥10 commits
    AND is idle (last touch >90 days ago)."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("oc")
    # Pin the recent cutoff so Orphan + Solitaire / activity-classifier
    # paths agree on "what counts as old".
    graph.__dict__["recent_cutoff"] = now - timedelta(days=90)
    bob = make_account("Bob", "b@x", project.ref())
    graph.git_accounts.add(bob)
    # 12 lifetime commits to spread across files (≥orphancauser_min_lifetime_commits=10).
    files = [make_file(f"src/old_{i}.py", project.ref()) for i in range(4)]
    for ff in files:
        graph.files.add(ff)
    cid = 0
    for ff in files:
        for _ in range(3):
            cid += 1
            c = make_commit(
                f"old_{cid}", "feat", bob,
                now - timedelta(days=300 + cid), project.ref(),
            )
            graph.commits.add(c)
            add_change(graph, c, ff, added=10)

    _run(graph)
    # Sanity — each file is Orphan (single author + stale).
    for ff in files:
        traits = _traits_for_file(graph, ff.path)
        assert _trait(traits, "anomaly.knowledge.Orphan") is not None

    bob_traits = _traits_for_account(graph, bob.id)
    t = _trait(bob_traits, "anomaly.knowledge.OrphanCausers")
    assert t is not None
    assert t.evidence["orphan_files_count"] == 4
    assert t.evidence["lifetime_commits"] >= 10
    assert t.evidence["threshold"] == 3


def test_orphan_causers_suppressed_for_active_author():
    """Same orphan-files topology but the author still has recent commits → idle=False → no trait."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("oc_neg")
    bob = make_account("Bob", "b@x", project.ref())
    graph.git_accounts.add(bob)
    files = [make_file(f"src/x_{i}.py", project.ref()) for i in range(4)]
    for ff in files:
        graph.files.add(ff)
    cid = 0
    for ff in files:
        for _ in range(3):
            cid += 1
            c = make_commit(
                f"x_{cid}", "feat", bob,
                now - timedelta(days=300 + cid), project.ref(),
            )
            graph.commits.add(c)
            add_change(graph, c, ff, added=10)
    # Make Bob "active" by giving him a single recent commit on a separate
    # active file (no Orphan trait on the active file because it's recent).
    active_file = make_file("src/active.py", project.ref())
    graph.files.add(active_file)
    c = make_commit(
        "recent", "feat", bob, now - timedelta(days=3), project.ref(),
    )
    graph.commits.add(c)
    add_change(graph, c, active_file, added=5)

    _run(graph)
    bob_traits = _traits_for_account(graph, bob.id)
    assert "anomaly.knowledge.OrphanCausers" not in _names(bob_traits)
