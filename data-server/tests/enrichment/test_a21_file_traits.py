"""A2.1 file-level git-only anomaly traits — v2 port.

Restored from ``git show f840488^:data-server/tests/enrichment/test_a21_file_traits.py``
and ported to the v2 :class:`Graph` surface. The legacy version built
``GitProject(...)`` with embedded registries; v2 builds a typed
:class:`Graph`, populates ``graph.commits`` / ``graph.files`` /
``graph.changes`` directly, runs ``run_pipeline``, and asserts against
``graph.traits`` / ``graph.classifiers``.

Each test targets exactly one trait so the assertion is meaningful even
when threshold defaults change.

Status (Chunk 11)
-----------------
This file is **restored as the regression checklist** for Chunks 15
(anomaly_cohesion / anomaly_testing / anomaly_structuring) and Chunk 16
(anomaly_knowledge). Every test is :func:`pytest.mark.xfail`-marked
with the chunk that is expected to make it pass. As each downstream
chunk lands the implementing chunk drops the xfail marker.

The xfail markers use ``strict=False`` so a precocious early
implementation does not break this file. The chunk that flips a
``NotImplementedError`` stub to a real metric is responsible for
removing the corresponding ``xfail``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from src.common.kernel import Graph
from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import run_pipeline
from src.enrichment.tags import Trait

from tests.enrichment.conftest import (
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)


UTC = timezone.utc


# Markers — each pending chunk flips its tests to passing.
PENDING_KNOWLEDGE = pytest.mark.xfail(
    reason="Chunk 16 ports anomaly_knowledge — NotImplementedError today.",
    strict=False,
)
PENDING_COHESION_ACTIVITY = pytest.mark.xfail(
    reason="Chunk 15b will port anomaly.cohesion.activity.* (Hibernator / "
    "Awakening / Erosion). 15a shipped coordination + size only — see "
    "handoffs_followup/chunk_15_heavy_anomalies.md §Split.",
    strict=False,
)


# ----------------------------------------------------------------------
# Helpers — v2 trait lookup on graph.traits
# ----------------------------------------------------------------------
def _traits_for_file(graph: Graph, file_id: str) -> tuple[Trait, ...]:
    from src.common.kernel import EntityKind, EntityRef
    return graph.traits.for_target(EntityRef(kind=EntityKind.FILE, id=file_id))


def _trait(traits, name: str) -> Optional[Trait]:
    return next((t for t in traits if t.name == name), None)


def _trait_names(traits) -> set[str]:
    return {t.name for t in traits}


def _run(graph: Graph) -> None:
    """Run the full pipeline on ``graph`` (catches per-metric errors)."""
    run_pipeline(graph, EnrichmentConfig())


# ----------------------------------------------------------------------
# Knowledge family — Chunk 16
# ----------------------------------------------------------------------
@PENDING_KNOWLEDGE
def test_accumulator_emitted_when_many_positive_windows():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("acc")
    alice = make_account("Alice", "alice@example.com", project.ref())
    graph.git_accounts.add(alice)

    f = make_file("src/grow.py", project.ref())
    graph.files.add(f)

    # 8 commits, each in its own 4-week bucket, each net additive.
    for i in range(8):
        c = make_commit(f"c_{i}", f"feat: pass {i}", alice,
                        now - timedelta(weeks=i * 4 + 1), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=20, deleted=2)

    _run(graph)
    traits = _traits_for_file(graph, "src/grow.py")
    t = _trait(traits, "anomaly.knowledge.Accumulator")
    assert t is not None
    assert t.evidence["positive_windows"] >= 6


@PENDING_KNOWLEDGE
def test_polarised_ownership_emitted_with_two_dominant_authors():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("pol")
    alice = make_account("Alice", "alice@example.com", project.ref())
    bob = make_account("Bob", "bob@example.com", project.ref())
    carol = make_account("Carol", "carol@example.com", project.ref())
    for a in (alice, bob, carol):
        graph.git_accounts.add(a)

    f = make_file("src/two.py", project.ref())
    graph.files.add(f)

    for i in range(2):
        c = make_commit(f"a{i}", "feat", alice, now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=25)
    for i in range(2):
        c = make_commit(f"b{i}", "feat", bob, now - timedelta(days=20 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=25)
    c = make_commit("c1", "fix typo", carol, now - timedelta(days=30), project.ref())
    graph.commits.add(c)
    add_change(graph, c, f, added=5)

    _run(graph)
    traits = _traits_for_file(graph, "src/two.py")
    names = _trait_names(traits)
    assert "anomaly.knowledge.PolarisedOwnership" in names
    assert "anomaly.knowledge.BusFactor1" not in names


@PENDING_KNOWLEDGE
def test_owner_churn_emitted_when_dominant_author_changes():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("oc")
    alice = make_account("Alice", "alice@example.com", project.ref())
    bob = make_account("Bob", "bob@example.com", project.ref())
    for a in (alice, bob):
        graph.git_accounts.add(a)
    f = make_file("src/oc.py", project.ref())
    graph.files.add(f)

    for i in range(5):
        c = make_commit(f"old_{i}", "feat", alice, now - timedelta(days=200 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=100)
    for i in range(3):
        c = make_commit(f"new_{i}", "feat", bob, now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=50)

    _run(graph)
    traits = _traits_for_file(graph, "src/oc.py")
    t = _trait(traits, "anomaly.knowledge.OwnerChurn")
    assert t is not None
    assert t.evidence["lifetime_owner"] != t.evidence["recent_owner"]


@PENDING_KNOWLEDGE
def test_solitaire_emitted_when_one_active_rest_idle():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("sol")
    alice = make_account("Alice", "alice@example.com", project.ref())
    bob = make_account("Bob", "bob@example.com", project.ref())
    for a in (alice, bob):
        graph.git_accounts.add(a)
    f = make_file("src/sol.py", project.ref())
    graph.files.add(f)

    for i in range(2):
        c = make_commit(f"bob_{i}", "feat", bob, now - timedelta(days=300 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=10)
    for i in range(5):
        c = make_commit(f"al_{i}", "feat", alice, now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=10)

    _run(graph)
    traits = _traits_for_file(graph, "src/sol.py")
    assert _trait(traits, "anomaly.knowledge.Solitaire") is not None


@PENDING_KNOWLEDGE
def test_team_churn_emitted_when_recent_set_differs():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("tc")
    a = make_account("A", "a@x", project.ref())
    b = make_account("B", "b@x", project.ref())
    c_ = make_account("C", "c@x", project.ref())
    d = make_account("D", "d@x", project.ref())
    for acc in (a, b, c_, d):
        graph.git_accounts.add(acc)
    f = make_file("src/tc.py", project.ref())
    graph.files.add(f)

    for i, auth in enumerate([a, b]):
        cm = make_commit(f"old_{i}", "feat", auth, now - timedelta(days=200 + i), project.ref())
        graph.commits.add(cm)
        add_change(graph, cm, f, added=10)
    for i, auth in enumerate([c_, d]):
        cm = make_commit(f"new_{i}", "feat", auth, now - timedelta(days=10 + i), project.ref())
        graph.commits.add(cm)
        add_change(graph, cm, f, added=10)

    _run(graph)
    traits = _traits_for_file(graph, "src/tc.py")
    t = _trait(traits, "anomaly.knowledge.TeamChurn")
    assert t is not None
    assert t.evidence["jaccard_distance"] >= 0.5


@PENDING_KNOWLEDGE
def test_weak_ownership_does_not_fire_for_all_active_authors():
    """All recent churn from active authors → WeakOwnership must NOT fire."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("wo")
    a1 = make_account("A1", "a1@x", project.ref())
    a2 = make_account("A2", "a2@x", project.ref())
    for a in (a1, a2):
        graph.git_accounts.add(a)
    f = make_file("src/wo.py", project.ref())
    graph.files.add(f)

    for i, auth in enumerate([a1, a2]):
        cm = make_commit(f"f_{i}", "feat", auth, now - timedelta(days=5 + i), project.ref())
        graph.commits.add(cm)
        add_change(graph, cm, f, added=20)

    _run(graph)
    traits = _traits_for_file(graph, "src/wo.py")
    assert _trait(traits, "anomaly.knowledge.WeakOwnership") is None


# ----------------------------------------------------------------------
# Cohesion family — Chunk 15
# ----------------------------------------------------------------------
@PENDING_COHESION_ACTIVITY
def test_hibernator_emitted_on_dormant_file():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("hib")
    alice = make_account("Alice", "alice@example.com", project.ref())
    graph.git_accounts.add(alice)

    f = make_file("src/hib.py", project.ref())
    other = make_file("src/recent.py", project.ref())
    for ff in (f, other):
        graph.files.add(ff)

    for i in range(6):
        c = make_commit(f"old_{i}", "feat", alice, now - timedelta(days=400 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=10)

    c = make_commit("c_recent", "chore", alice, now - timedelta(days=5), project.ref())
    graph.commits.add(c)
    add_change(graph, c, other, added=1)

    _run(graph)
    traits = _traits_for_file(graph, "src/hib.py")
    t = _trait(traits, "anomaly.cohesion.activity.Hibernator")
    assert t is not None
    assert t.evidence["lifetime_commits"] == 6


@PENDING_COHESION_ACTIVITY
def test_awakening_emitted_when_dormant_then_recent():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("awa")
    alice = make_account("Alice", "alice@example.com", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/awa.py", project.ref())
    graph.files.add(f)

    c1 = make_commit("old", "feat", alice, now - timedelta(days=400), project.ref())
    graph.commits.add(c1)
    add_change(graph, c1, f, added=20)
    c2 = make_commit("new", "feat", alice, now - timedelta(days=5), project.ref())
    graph.commits.add(c2)
    add_change(graph, c2, f, added=15)

    _run(graph)
    traits = _traits_for_file(graph, "src/awa.py")
    t = _trait(traits, "anomaly.cohesion.activity.Awakening")
    assert t is not None
    assert t.evidence["dormant_days"] >= 7 * 12


@PENDING_COHESION_ACTIVITY
def test_erosion_emitted_on_declining_trend():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("er")
    alice = make_account("Alice", "alice@example.com", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/er.py", project.ref())
    graph.files.add(f)

    schedule = [(20, 8), (16, 6), (12, 4), (8, 2), (4, 1)]
    cid = 0
    for weeks_back, count in schedule:
        for _ in range(count):
            cid += 1
            c = make_commit(f"e_{cid}", "feat", alice,
                            now - timedelta(weeks=weeks_back, days=cid), project.ref())
            graph.commits.add(c)
            add_change(graph, c, f, added=5)

    _run(graph)
    traits = _traits_for_file(graph, "src/er.py")
    t = _trait(traits, "anomaly.cohesion.activity.Erosion")
    assert t is not None
    assert t.evidence["slope"] <= EnrichmentConfig().erosion_trend_max


def test_flicker_emitted_on_volatile_recent_window():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("fl")
    alice = make_account("Alice", "alice@example.com", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/fl.py", project.ref())
    graph.files.add(f)

    days_back = [80, 79, 78, 77, 5, 4]
    for i, d in enumerate(days_back):
        c = make_commit(f"f_{i}", "feat", alice, now - timedelta(days=d), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)

    _run(graph)
    traits = _traits_for_file(graph, "src/fl.py")
    t = _trait(traits, "anomaly.cohesion.coordination.Flicker")
    assert t is not None
    assert t.evidence["recent_interval_cv"] >= 1.2


def test_frequent_changer_lifetime():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("fc")
    alice = make_account("Alice", "alice@example.com", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/fc.py", project.ref())
    graph.files.add(f)

    for i in range(50):
        c = make_commit(f"f_{i}", "feat", alice, now - timedelta(days=300 - i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=2)

    _run(graph)
    traits = _traits_for_file(graph, "src/fc.py")
    t = _trait(traits, "anomaly.cohesion.size.FrequentChanger")
    assert t is not None
    assert t.evidence["basis"] in ("lifetime", "recent")
    assert t.evidence["lifetime_commits"] >= 50


# ----------------------------------------------------------------------
# Testing family — Chunk 15
# ----------------------------------------------------------------------
def test_refactoring_magnet_emitted_on_many_refactor_commits():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("rm")
    alice = make_account("Alice", "alice@example.com", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/rm.py", project.ref())
    graph.files.add(f)

    for i in range(12):
        c = make_commit(f"r_{i}", f"refactor: simplify {i}", alice,
                        now - timedelta(days=100 - i * 5), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5, deleted=5)

    _run(graph)
    traits = _traits_for_file(graph, "src/rm.py")
    t = _trait(traits, "anomaly.testing.RefactoringMagnet")
    assert t is not None
    assert t.evidence["refactor_commits"] >= 10


# ----------------------------------------------------------------------
# Structuring family — Chunk 15
# ----------------------------------------------------------------------
def test_identical_filenames_emitted_per_file():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("idf")
    alice = make_account("Alice", "alice@example.com", project.ref())
    graph.git_accounts.add(alice)

    paths_files = [
        make_file("src/a/utils.py", project.ref()),
        make_file("src/b/utils.py", project.ref()),
        make_file("src/c/utils.py", project.ref()),
        make_file("src/d/loner.py", project.ref()),
    ]
    for f in paths_files:
        graph.files.add(f)

    for i, file_ in enumerate(paths_files):
        c = make_commit(f"i_{i}", "feat", alice, now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, file_, added=5)

    _run(graph)
    for path in ["src/a/utils.py", "src/b/utils.py", "src/c/utils.py"]:
        traits = _traits_for_file(graph, path)
        t = _trait(traits, "anomaly.structuring.IdenticalFilenames")
        assert t is not None
        assert t.evidence["basename"] == "utils.py"
        assert t.evidence["peer_count"] == 2

    loner_traits = _traits_for_file(graph, "src/d/loner.py")
    assert _trait(loner_traits, "anomaly.structuring.IdenticalFilenames") is None
