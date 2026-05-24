"""Tests for :class:`AuthorClassifierMetric` (Chunk 12).

The legacy suite did not ship a dedicated author-classifier test file
(``test_a22_author_traits.py`` covers ``OrphanCausers``, which is part
of ``anomaly_knowledge`` and lands in Chunk 16). These tests are
v2-native and cover the two emitted classifier dimensions:

* ``activity``   ∈ {``active``, ``idle``}
* ``seniority``  ∈ {``newcomer``, ``established``, ``senior``, ``veteran``}

The ``activity`` dimension is load-bearing for
:func:`file_trait_utils.active_author_churn` (Chunk-11 handoff locks
the dimension name as ``"activity"``).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from src.enrichment.config import EnrichmentConfig
from src.enrichment.metrics import METRICS
from src.enrichment.metrics.implementations.author_classifiers import (
    AuthorClassifierMetric,
)
from src.enrichment.tags import Classifier

from tests.enrichment.conftest import (
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)


UTC = timezone.utc


def _classifier_for(
    graph, account, dimension: str
) -> Optional[Classifier]:
    """Pick the (single) classifier on ``account`` along ``dimension``."""
    for cls in graph.classifiers:
        if cls.target == account.ref() and cls.dimension == dimension:
            return cls
    return None


# ----------------------------------------------------------------------
# Catalog wiring
# ----------------------------------------------------------------------
def test_metric_is_registered():
    assert "author.classifiers" in METRICS
    assert METRICS.get("author.classifiers") is AuthorClassifierMetric


def test_metric_metadata_emits_two_classifiers():
    assert sorted(AuthorClassifierMetric.outputs.emits_classifiers) == [
        "activity",
        "seniority",
    ]


# ----------------------------------------------------------------------
# Activity dimension — active when last commit inside recent window.
# ----------------------------------------------------------------------
def test_active_when_last_commit_within_window():
    """An author with a commit ≤ recent_window_days ago is ``active``."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("act-active")
    alice = make_account("Alice", "alice@example.com", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/a.py", project.ref())
    graph.files.add(f)
    c = make_commit("a1", "feat", alice, now - timedelta(days=10), project.ref())
    graph.commits.add(c)
    add_change(graph, c, f)

    out = list(AuthorClassifierMetric().compute(graph, EnrichmentConfig()))
    # Persist them so the per-account lookup matches what run_pipeline writes.
    for cls in out:
        graph.classifiers.add(cls)

    activity = _classifier_for(graph, alice, "activity")
    assert activity is not None
    assert activity.value == "active"


def test_idle_when_last_commit_outside_window():
    """An author whose last commit is older than recent_window_days is ``idle``."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("act-idle")
    eve = make_account("Eve", "eve@example.com", project.ref())
    # Add a second active author so the latest commit anchor sits in the
    # recent window — otherwise eve's only-old commits become "recent"
    # relative to themselves and activity flips to active.
    anchor_author = make_account("Anchor", "anchor@example.com", project.ref())
    for a in (eve, anchor_author):
        graph.git_accounts.add(a)
    f = make_file("src/e.py", project.ref())
    graph.files.add(f)

    old = make_commit("eve_old", "feat", eve, now - timedelta(days=300), project.ref())
    graph.commits.add(old)
    add_change(graph, old, f)

    fresh = make_commit("anchor_new", "feat", anchor_author, now - timedelta(days=5), project.ref())
    graph.commits.add(fresh)
    add_change(graph, fresh, f)

    out = list(AuthorClassifierMetric().compute(graph, EnrichmentConfig(recent_window_days=90)))
    for cls in out:
        graph.classifiers.add(cls)

    eve_activity = _classifier_for(graph, eve, "activity")
    assert eve_activity is not None
    assert eve_activity.value == "idle"

    anchor_activity = _classifier_for(graph, anchor_author, "activity")
    assert anchor_activity is not None
    assert anchor_activity.value == "active"


def test_no_commits_yields_no_classifier():
    """An account with zero commits emits no classifier at all."""
    graph, project = build_v2_graph("act-empty")
    bob = make_account("Bob", "bob@example.com", project.ref())
    graph.git_accounts.add(bob)

    out = list(AuthorClassifierMetric().compute(graph, EnrichmentConfig()))
    assert out == []


# ----------------------------------------------------------------------
# Seniority dimension — bucketed by first→last commit span (days).
# ----------------------------------------------------------------------
def test_seniority_newcomer_when_span_within_newcomer_max():
    """Default ``newcomer_max_days = 30``."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("sen-new")
    nat = make_account("Nat", "nat@example.com", project.ref())
    graph.git_accounts.add(nat)
    f = make_file("src/n.py", project.ref())
    graph.files.add(f)

    c1 = make_commit("n1", "feat", nat, now - timedelta(days=15), project.ref())
    c2 = make_commit("n2", "feat", nat, now - timedelta(days=5), project.ref())
    for c in (c1, c2):
        graph.commits.add(c)
        add_change(graph, c, f)

    out = list(AuthorClassifierMetric().compute(graph, EnrichmentConfig()))
    for cls in out:
        graph.classifiers.add(cls)

    sen = _classifier_for(graph, nat, "seniority")
    assert sen is not None
    assert sen.value == "newcomer"


def test_seniority_established_at_120_days():
    """Default ``newcomer_max=30 < 120 <= established_max=180`` → established."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("sen-est")
    es = make_account("Es", "es@example.com", project.ref())
    graph.git_accounts.add(es)
    f = make_file("src/e.py", project.ref())
    graph.files.add(f)

    c1 = make_commit("e1", "feat", es, now - timedelta(days=125), project.ref())
    c2 = make_commit("e2", "feat", es, now - timedelta(days=5), project.ref())
    for c in (c1, c2):
        graph.commits.add(c)
        add_change(graph, c, f)

    out = list(AuthorClassifierMetric().compute(graph, EnrichmentConfig()))
    for cls in out:
        graph.classifiers.add(cls)

    sen = _classifier_for(graph, es, "seniority")
    assert sen is not None
    assert sen.value == "established"


def test_seniority_senior_at_400_days():
    """Default ``established_max=180 < 400 <= senior_max=730`` → senior."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("sen-sen")
    sr = make_account("Sr", "sr@example.com", project.ref())
    graph.git_accounts.add(sr)
    f = make_file("src/s.py", project.ref())
    graph.files.add(f)

    c1 = make_commit("s1", "feat", sr, now - timedelta(days=410), project.ref())
    c2 = make_commit("s2", "feat", sr, now - timedelta(days=5), project.ref())
    for c in (c1, c2):
        graph.commits.add(c)
        add_change(graph, c, f)

    out = list(AuthorClassifierMetric().compute(graph, EnrichmentConfig()))
    for cls in out:
        graph.classifiers.add(cls)

    sen = _classifier_for(graph, sr, "seniority")
    assert sen is not None
    assert sen.value == "senior"


def test_seniority_veteran_beyond_senior_max():
    """Span > 730 days → veteran."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("sen-vet")
    vt = make_account("Vt", "vt@example.com", project.ref())
    graph.git_accounts.add(vt)
    f = make_file("src/v.py", project.ref())
    graph.files.add(f)

    c1 = make_commit("v1", "feat", vt, now - timedelta(days=1000), project.ref())
    c2 = make_commit("v2", "feat", vt, now - timedelta(days=5), project.ref())
    for c in (c1, c2):
        graph.commits.add(c)
        add_change(graph, c, f)

    out = list(AuthorClassifierMetric().compute(graph, EnrichmentConfig()))
    for cls in out:
        graph.classifiers.add(cls)

    sen = _classifier_for(graph, vt, "seniority")
    assert sen is not None
    assert sen.value == "veteran"


# ----------------------------------------------------------------------
# Locked contract for downstream consumers
# ----------------------------------------------------------------------
def test_activity_active_classifier_is_readable_via_classifier_registry():
    """``active_author_churn`` looks up by dimension ``"activity"`` + value
    ``"active"``. Lock the contract: the metric MUST emit the exact pair
    so the helper's filter still works.
    """
    now = datetime.now(UTC)
    graph, project = build_v2_graph("act-contract")
    alice = make_account("Alice", "alice@example.com", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/a.py", project.ref())
    graph.files.add(f)
    c = make_commit("a1", "feat", alice, now - timedelta(days=10), project.ref())
    graph.commits.add(c)
    add_change(graph, c, f)

    for cls in AuthorClassifierMetric().compute(graph, EnrichmentConfig()):
        graph.classifiers.add(cls)

    actives = graph.classifiers.with_value("activity", "active")
    assert any(c.target == alice.ref() for c in actives)
