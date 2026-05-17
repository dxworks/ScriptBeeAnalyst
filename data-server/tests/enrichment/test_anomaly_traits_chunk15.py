"""Anomaly trait emission tests — v2 port, Chunk 15 (heavy anomalies).

Covers the three metrics ported by this chunk:

* :class:`AnomalyTestingMetric`     — BugMagnet, RefactoringMagnet, TestOrphan
* :class:`AnomalyStructuringMetric` — IdenticalFilenames, PivotFile (cochange),
                                      TasksBottleneck (issue + author scopes)
* :class:`AnomalyCohesionMetric` (15a) — Bazaar, Cathedral, Pulsar, Flicker,
                                      Supernova (proxy), FrequentChanger

Activity sub-family (Hibernator / Awakening / Erosion) is deferred to 15b
— those tests live in ``test_a21_file_traits.py`` under
``PENDING_COHESION_ACTIVITY`` xfail.

Each test isolates one metric via direct ``metric.compute(...)`` consumption
rather than running the full pipeline so a single failure is easy to pin.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind
from src.common.domains.jira.models import (
    Issue,
    IssueStatus,
    IssueType,
    JiraProject,
    JiraUser,
)
from src.enrichment.config import EnrichmentConfig
from src.enrichment.metrics.implementations.anomaly_cohesion import (
    AnomalyCohesionMetric,
)
from src.enrichment.metrics.implementations.anomaly_structuring import (
    AnomalyStructuringMetric,
)
from src.enrichment.metrics.implementations.anomaly_testing import (
    AnomalyTestingMetric,
)
from src.enrichment.metrics.implementations.commit_classifiers import (
    CommitClassifierMetric,
)
from src.enrichment.metrics.implementations.file_classifiers import (
    FileClassifierMetric,
)
from src.enrichment.relations import Relation, WindowKind
from src.enrichment.tags import TraitFamily

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
def _consume_metric(graph, metric, config=None) -> None:
    """Drain a metric into the graph (auto-routes Trait / Classifier)."""
    from src.enrichment.tags import Classifier as _C, Trait as _T
    cfg = config if config is not None else EnrichmentConfig()
    for emitted in metric.compute(graph, cfg):
        if isinstance(emitted, _T):
            graph.traits.add(emitted)
        elif isinstance(emitted, _C):
            graph.classifiers.add(emitted)


def _trait_names(traits: Iterable[Any]) -> set[str]:
    return {t.name for t in traits}


# ======================================================================
# AnomalyTestingMetric
# ======================================================================
def test_bugmagnet_emitted_on_buggy_file():
    """File with 5+ bugfix-nature commits and ratio >= 0.40 → BugMagnet."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("bm-pos")
    alice = make_account("Alice", "alice@example.com", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/buggy.py", project.ref())
    graph.files.add(f)

    # 5 bugfix commits + 3 unrelated feat commits.
    for i in range(5):
        c = make_commit(f"b_{i}", f"fix: leak {i}", alice,
                        now - timedelta(days=20 - i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)
    for i in range(3):
        c = make_commit(f"x_{i}", f"feat: add {i}", alice,
                        now - timedelta(days=30 - i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)

    # Classifiers must run before the testing metric.
    _consume_metric(graph, FileClassifierMetric())
    _consume_metric(graph, CommitClassifierMetric())
    _consume_metric(graph, AnomalyTestingMetric())

    traits = graph.traits.for_target(f.ref())
    names = _trait_names(traits)
    assert "anomaly.testing.BugMagnet" in names
    bm = next(t for t in traits if t.name == "anomaly.testing.BugMagnet")
    assert bm.evidence["bugfix_commits"] == 5
    assert bm.evidence["total_commits"] == 8
    assert 0.6 <= bm.evidence["bugfix_ratio"] <= 0.7
    assert bm.family == TraitFamily.TESTING


def test_bugmagnet_threshold_overrides_suppress_emission():
    """Raising bugmagnet_ratio_min above the file's bugfix ratio must suppress."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("bm-thr")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/x.py", project.ref())
    graph.files.add(f)
    # 5 bugfix commits + 10 feat commits → ratio = 5/15 ≈ 0.33.
    for i in range(5):
        c = make_commit(f"b_{i}", f"fix: leak {i}", alice,
                        now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)
    for i in range(10):
        c = make_commit(f"x_{i}", f"feat: x {i}", alice,
                        now - timedelta(days=30 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)

    _consume_metric(graph, FileClassifierMetric())
    _consume_metric(graph, CommitClassifierMetric())
    # Default ratio_min=0.40 already suppresses; bump higher to lock in.
    _consume_metric(
        graph, AnomalyTestingMetric(),
        EnrichmentConfig(bugmagnet_ratio_min=0.50),
    )
    assert graph.traits.of_name("anomaly.testing.BugMagnet") == ()


def test_refactoring_magnet_threshold_suppression():
    """Refactor count below threshold → no trait."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("rm-low")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/r.py", project.ref())
    graph.files.add(f)
    for i in range(3):  # below default refactoring_magnet_min_commits=10
        c = make_commit(f"r_{i}", f"refactor: simplify {i}", alice,
                        now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)

    _consume_metric(graph, FileClassifierMetric())
    _consume_metric(graph, CommitClassifierMetric())
    _consume_metric(graph, AnomalyTestingMetric())
    assert graph.traits.of_name("anomaly.testing.RefactoringMagnet") == ()


def test_test_orphan_fires_on_production_file_without_test_cochange():
    """Production file with several commits + zero test-file cochange → TestOrphan."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("orphan-pos")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)

    prod = make_file("src/prod.py", project.ref())
    test_file = make_file("tests/test_prod.py", project.ref())
    for ff in (prod, test_file):
        graph.files.add(ff)

    # 4 production-only commits.
    for i in range(4):
        c = make_commit(f"p_{i}", f"feat: prod {i}", alice,
                        now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, prod, added=10)

    # Separate test-file commit (no cochange with prod).
    tc = make_commit("t1", "test: bootstrap", alice, now - timedelta(days=2), project.ref())
    graph.commits.add(tc)
    add_change(graph, tc, test_file, added=5)

    _consume_metric(graph, FileClassifierMetric())
    _consume_metric(graph, CommitClassifierMetric())
    _consume_metric(graph, AnomalyTestingMetric())

    traits = graph.traits.for_target(prod.ref())
    to = next((t for t in traits if t.name == "anomaly.testing.TestOrphan"), None)
    assert to is not None
    assert to.is_proxy is True
    assert to.evidence["proxy"] is True
    assert to.evidence["cochange_test_count"] == 0


def test_test_orphan_suppressed_when_no_test_files():
    """Zero test-role files → guard skips emission entirely."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("orphan-empty")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    prod = make_file("src/p.py", project.ref())
    graph.files.add(prod)
    for i in range(4):
        c = make_commit(f"p_{i}", f"feat: p {i}", alice,
                        now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, prod, added=10)
    _consume_metric(graph, FileClassifierMetric())
    _consume_metric(graph, CommitClassifierMetric())
    _consume_metric(graph, AnomalyTestingMetric())
    assert graph.traits.of_name("anomaly.testing.TestOrphan") == ()


def test_test_orphan_suppressed_when_cochanges_with_test():
    """Production file co-changes with the test file > threshold → no trait."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("orphan-cochange")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    prod = make_file("src/p.py", project.ref())
    tf = make_file("tests/test_p.py", project.ref())
    for ff in (prod, tf):
        graph.files.add(ff)
    for i in range(5):
        c = make_commit(f"c_{i}", f"feat: p {i}", alice,
                        now - timedelta(days=10 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, prod, added=10)
        add_change(graph, c, tf, added=4)
    _consume_metric(graph, FileClassifierMetric())
    _consume_metric(graph, CommitClassifierMetric())
    _consume_metric(graph, AnomalyTestingMetric())
    assert graph.traits.of_name("anomaly.testing.TestOrphan") == ()


# ======================================================================
# AnomalyStructuringMetric — PivotFile (cochange basis)
# ======================================================================
def test_pivotfile_cochange_fires_above_threshold():
    """File with 12 cochange peers exceeds default pivotfile threshold (10)."""
    graph, project = build_v2_graph("pf-cochange")
    hub = make_file("src/hub.py", project.ref())
    graph.files.add(hub)
    for i in range(12):
        peer = make_file(f"src/p_{i}.py", project.ref())
        graph.files.add(peer)
        graph.relations.add(Relation(
            id=Relation.canonical_id(
                hub.ref(), peer.ref(), "cochange", WindowKind.LIFETIME,
            ),
            source=hub.ref(), target=peer.ref(),
            relation_kind="cochange", window=WindowKind.LIFETIME, strength=1.0,
        ))

    _consume_metric(graph, AnomalyStructuringMetric())

    hub_traits = [
        t for t in graph.traits.of_name("anomaly.structuring.PivotFile")
        if t.target == hub.ref()
    ]
    assert len(hub_traits) == 1
    assert hub_traits[0].evidence["basis"] == "cochange"
    assert hub_traits[0].evidence["cochange_degree"] == 12


def test_pivotfile_cochange_silent_without_relations():
    """No cochange relations → no trait, no error."""
    graph, _ = build_v2_graph("pf-empty")
    _consume_metric(graph, AnomalyStructuringMetric())
    assert graph.traits.of_name("anomaly.structuring.PivotFile") == ()


# ======================================================================
# AnomalyStructuringMetric — IdenticalFilenames
# ======================================================================
def test_identical_filenames_per_file_emission():
    graph, project = build_v2_graph("idf")
    files = [
        make_file("a/util.py", project.ref()),
        make_file("b/util.py", project.ref()),
        make_file("c/util.py", project.ref()),
        make_file("d/loner.py", project.ref()),
    ]
    for f in files:
        graph.files.add(f)
    _consume_metric(graph, AnomalyStructuringMetric())
    for path in ["a/util.py", "b/util.py", "c/util.py"]:
        t = graph.traits.for_target(EntityRef(kind=EntityKind.FILE, id=path))
        idf = [x for x in t if x.name == "anomaly.structuring.IdenticalFilenames"]
        assert len(idf) == 1
        assert idf[0].evidence["basename"] == "util.py"
        assert idf[0].evidence["peer_count"] == 2
        assert sorted(idf[0].evidence["peer_file_ids"]) != []
    loner = graph.traits.for_target(EntityRef(kind=EntityKind.FILE, id="d/loner.py"))
    assert all(t.name != "anomaly.structuring.IdenticalFilenames" for t in loner)


def test_identical_filenames_peer_cap():
    """peer_file_ids is trimmed to peer_cap entries."""
    graph, project = build_v2_graph("idf-cap")
    for i in range(5):
        f = make_file(f"d{i}/x.py", project.ref())
        graph.files.add(f)
    _consume_metric(
        graph, AnomalyStructuringMetric(),
        EnrichmentConfig(identical_filenames_peer_cap=2),
    )
    sample_ref = EntityRef(kind=EntityKind.FILE, id="d0/x.py")
    t = next(
        x for x in graph.traits.for_target(sample_ref)
        if x.name == "anomaly.structuring.IdenticalFilenames"
    )
    # peer_count is the true 4; peer_file_ids list capped at 2.
    assert t.evidence["peer_count"] == 4
    assert len(t.evidence["peer_file_ids"]) == 2


# ======================================================================
# AnomalyStructuringMetric — TasksBottleneck
# ======================================================================
def _jira_project(graph) -> JiraProject:
    jp = JiraProject(id="jp:test", name="jira", source=SourceKind.JIRA)
    graph.jira_projects.add(jp)
    return jp


def _make_status(graph, jp, name: str, category: str) -> IssueStatus:
    s = IssueStatus(id=f"st:{name}", project_ref=jp.ref(), name=name, category=category)
    graph.issue_statuses.add(s)
    return s


def _make_type(graph, jp) -> IssueType:
    t = IssueType(id="it:task", project_ref=jp.ref(), name="Task")
    graph.issue_types.add(t)
    return t


def _make_issue(
    graph, jp, key: str, created_days_ago: int, status: IssueStatus,
    type_: IssueType, assignee_refs=None,
) -> Issue:
    now = datetime.now(UTC)
    issue = Issue(
        id=key, project_ref=jp.ref(), key=key, summary=key,
        created_at=now - timedelta(days=created_days_ago),
        updated_at=now,
        status_ref=status.ref(), type_ref=type_.ref(),
        assignee_refs=list(assignee_refs or []),
    )
    graph.issues.add(issue)
    return issue


def test_tasks_bottleneck_issue_scope_fires_on_old_open_issue():
    graph, _ = build_v2_graph("tb-issue")
    jp = _jira_project(graph)
    open_st = _make_status(graph, jp, "Open", "new")
    it = _make_type(graph, jp)
    issue = _make_issue(graph, jp, "PROJ-1", created_days_ago=200, status=open_st, type_=it)
    _consume_metric(graph, AnomalyStructuringMetric())
    traits = graph.traits.for_target(issue.ref())
    names = _trait_names(traits)
    assert "anomaly.structuring.TasksBottleneck" in names
    tb = next(t for t in traits if t.name == "anomaly.structuring.TasksBottleneck")
    assert tb.evidence["scope"] == "issue"
    assert tb.evidence["open_age_days"] >= 180


def test_tasks_bottleneck_skips_resolved_issue():
    graph, _ = build_v2_graph("tb-resolved")
    jp = _jira_project(graph)
    done_st = _make_status(graph, jp, "Done", "done")
    it = _make_type(graph, jp)
    issue = _make_issue(graph, jp, "PROJ-2", 400, done_st, it)
    _consume_metric(graph, AnomalyStructuringMetric())
    assert graph.traits.for_target(issue.ref()) == ()


def test_tasks_bottleneck_author_scope_fires_on_high_in_flight():
    graph, _ = build_v2_graph("tb-author")
    jp = _jira_project(graph)
    open_st = _make_status(graph, jp, "Open", "new")
    it = _make_type(graph, jp)
    user = JiraUser(
        id="ju:alice", project_ref=jp.ref(), key="alice", name="alice",
    )
    graph.jira_users.add(user)
    for i in range(12):
        _make_issue(graph, jp, f"P-{i}", 1, open_st, it, assignee_refs=[user.ref()])
    _consume_metric(graph, AnomalyStructuringMetric())
    traits = graph.traits.for_target(user.ref())
    tb = [t for t in traits if t.evidence.get("scope") == "author"]
    assert len(tb) == 1
    assert tb[0].evidence["in_flight_issues"] == 12


# ======================================================================
# AnomalyCohesionMetric (Chunk 15a) — coordination + size
# ======================================================================
def test_bazaar_emitted_on_many_distinct_recent_authors():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("baz")
    f = make_file("src/baz.py", project.ref())
    graph.files.add(f)
    accounts = [
        make_account(f"D{i}", f"d{i}@x", project.ref())
        for i in range(6)
    ]
    for a in accounts:
        graph.git_accounts.add(a)
    for i, a in enumerate(accounts):
        c = make_commit(f"c_{i}", "feat", a, now - timedelta(days=5 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)
    _consume_metric(graph, AnomalyCohesionMetric())
    t = next(
        x for x in graph.traits.for_target(f.ref())
        if x.name == "anomaly.cohesion.coordination.Bazaar"
    )
    assert t.evidence["distinct_authors_recent"] == 6


def test_cathedral_emitted_on_dominant_recent_author():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("cath")
    f = make_file("src/cath.py", project.ref())
    graph.files.add(f)
    alice = make_account("Alice", "a@x", project.ref())
    bob = make_account("Bob", "b@x", project.ref())
    for a in (alice, bob):
        graph.git_accounts.add(a)
    # Alice does 9 / 10 recent → dominance 0.9 > 0.8.
    for i in range(9):
        c = make_commit(f"a_{i}", "feat", alice, now - timedelta(days=2 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)
    c = make_commit("b1", "feat", bob, now - timedelta(days=5), project.ref())
    graph.commits.add(c)
    add_change(graph, c, f, added=5)
    _consume_metric(graph, AnomalyCohesionMetric())
    t = next(
        x for x in graph.traits.for_target(f.ref())
        if x.name == "anomaly.cohesion.coordination.Cathedral"
    )
    assert t.evidence["dominance_ratio"] >= 0.8
    assert t.evidence["recent_commits"] == 10


def test_pulsar_emitted_on_bursty_intervals():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("pul")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/pul.py", project.ref())
    graph.files.add(f)
    # Two tight clusters separated by a wide gap → CV > 1.
    cluster_offsets = [200, 199, 198, 197, 40, 39, 38]
    for i, d in enumerate(cluster_offsets):
        c = make_commit(f"c_{i}", "feat", alice, now - timedelta(days=d), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)
    _consume_metric(graph, AnomalyCohesionMetric())
    t = next(
        x for x in graph.traits.for_target(f.ref())
        if x.name == "anomaly.cohesion.coordination.Pulsar"
    )
    assert t.evidence["interval_cv"] >= 1.0
    assert t.evidence["commits"] == len(cluster_offsets)


def test_supernova_emitted_above_net_churn_threshold():
    """Net-churn above threshold → Supernova fires with is_proxy=True."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("sn")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/sn.py", project.ref())
    graph.files.add(f)
    for i in range(20):
        c = make_commit(f"c_{i}", "feat", alice, now - timedelta(days=i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=300, deleted=0)
    _consume_metric(
        graph, AnomalyCohesionMetric(),
        EnrichmentConfig(supernova_net_churn_min=100),
    )
    t = next(
        x for x in graph.traits.for_target(f.ref())
        if x.name == "anomaly.cohesion.size.Supernova"
    )
    assert t.is_proxy is True
    assert t.evidence["proxy"] is True
    assert "net-churn" in t.evidence["note"]
    assert t.evidence["net_churn"] >= 100


def test_supernova_skipped_below_threshold():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("sn-low")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/q.py", project.ref())
    graph.files.add(f)
    for i in range(5):
        c = make_commit(f"c_{i}", "feat", alice, now - timedelta(days=i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=10)
    _consume_metric(graph, AnomalyCohesionMetric(),
                    EnrichmentConfig(supernova_net_churn_min=10**6))
    assert graph.traits.of_name("anomaly.cohesion.size.Supernova") == ()


def test_frequent_changer_basis_lifetime():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("fc-life")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/fc.py", project.ref())
    graph.files.add(f)
    # 60 commits, all old (outside recent window).
    for i in range(60):
        c = make_commit(f"c_{i}", "feat", alice,
                        now - timedelta(days=200 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=2)
    _consume_metric(graph, AnomalyCohesionMetric())
    t = next(
        x for x in graph.traits.for_target(f.ref())
        if x.name == "anomaly.cohesion.size.FrequentChanger"
    )
    assert t.evidence["basis"] == "lifetime"
    assert t.evidence["lifetime_commits"] == 60


def test_flicker_emitted_on_volatile_recent():
    now = datetime.now(UTC)
    graph, project = build_v2_graph("fl")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/fl.py", project.ref())
    graph.files.add(f)
    # 6 recent commits with very uneven spacing.
    days_back = [80, 79, 78, 77, 5, 4]
    for i, d in enumerate(days_back):
        c = make_commit(f"c_{i}", "feat", alice, now - timedelta(days=d), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)
    _consume_metric(graph, AnomalyCohesionMetric())
    t = next(
        x for x in graph.traits.for_target(f.ref())
        if x.name == "anomaly.cohesion.coordination.Flicker"
    )
    assert t.evidence["recent_interval_cv"] >= 1.2


# ======================================================================
# AnomalyCohesionMetric (Chunk 15b) — activity sub-family
# ======================================================================
def test_hibernator_emitted_when_no_recent_activity():
    """Lifetime-rich file with zero recent commits → Hibernator fires."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("hib-pos")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/old.py", project.ref())
    graph.files.add(f)
    # Anchor a recent commit elsewhere so the cutoff is well-defined.
    other = make_file("src/other.py", project.ref())
    graph.files.add(other)
    recent = make_commit("anchor", "feat", alice, now - timedelta(days=2), project.ref())
    graph.commits.add(recent)
    add_change(graph, recent, other, added=1)
    # 8 old commits on the target file — all outside the 90d recent window.
    for i in range(8):
        c = make_commit(f"old_{i}", "feat", alice,
                        now - timedelta(days=400 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=10)
    _consume_metric(graph, AnomalyCohesionMetric())
    t = next(
        x for x in graph.traits.for_target(f.ref())
        if x.name == "anomaly.cohesion.activity.Hibernator"
    )
    assert t.evidence["lifetime_commits"] == 8
    assert t.evidence["recent_commits"] == 0


def test_hibernator_skipped_when_below_lifetime_threshold():
    """Below ``hibernator_min_lifetime_commits`` lifetime → no emit."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("hib-low")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/tiny.py", project.ref())
    graph.files.add(f)
    # Anchor a recent commit on a different file.
    other = make_file("src/anchor.py", project.ref())
    graph.files.add(other)
    anchor = make_commit("anchor", "feat", alice, now - timedelta(days=2), project.ref())
    graph.commits.add(anchor)
    add_change(graph, anchor, other, added=1)
    # Only 2 old commits on the target file — below the default 5.
    for i in range(2):
        c = make_commit(f"o_{i}", "feat", alice,
                        now - timedelta(days=300 + i), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=5)
    _consume_metric(graph, AnomalyCohesionMetric())
    assert graph.traits.of_name("anomaly.cohesion.activity.Hibernator") == ()


def test_awakening_emitted_after_dormancy():
    """One old commit, long gap, then a recent commit → Awakening fires."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("awa-pos")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/wake.py", project.ref())
    graph.files.add(f)
    c_old = make_commit("old", "feat", alice,
                        now - timedelta(days=400), project.ref())
    graph.commits.add(c_old)
    add_change(graph, c_old, f, added=20)
    c_new = make_commit("new", "feat", alice,
                        now - timedelta(days=3), project.ref())
    graph.commits.add(c_new)
    add_change(graph, c_new, f, added=15)
    _consume_metric(graph, AnomalyCohesionMetric())
    t = next(
        x for x in graph.traits.for_target(f.ref())
        if x.name == "anomaly.cohesion.activity.Awakening"
    )
    # Default awakening_min_dormant_weeks = 12 → 84d minimum.
    assert t.evidence["dormant_days"] >= 84
    assert t.evidence["recent_commits"] == 1


def test_awakening_skipped_when_continuous_activity():
    """Continuous low-cadence commits inside the dormancy window → no emit."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("awa-neg")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/steady.py", project.ref())
    graph.files.add(f)
    # Commits every 2 weeks across the year — no 12-week dormancy gap.
    for i in range(26):
        c = make_commit(f"s_{i}", "feat", alice,
                        now - timedelta(weeks=2 * i + 1), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f, added=2)
    _consume_metric(graph, AnomalyCohesionMetric())
    assert graph.traits.of_name("anomaly.cohesion.activity.Awakening") == ()


def test_erosion_emitted_on_declining_cadence():
    """Per-window commit counts shrink monotonically → Erosion fires."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("er-pos")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/decay.py", project.ref())
    graph.files.add(f)
    # Five 4-week windows, descending bucket counts (8/6/4/2/1).
    schedule = [(20, 8), (16, 6), (12, 4), (8, 2), (4, 1)]
    cid = 0
    for weeks_back, count in schedule:
        for _ in range(count):
            cid += 1
            c = make_commit(f"e_{cid}", "feat", alice,
                            now - timedelta(weeks=weeks_back, days=cid),
                            project.ref())
            graph.commits.add(c)
            add_change(graph, c, f, added=5)
    _consume_metric(graph, AnomalyCohesionMetric())
    t = next(
        x for x in graph.traits.for_target(f.ref())
        if x.name == "anomaly.cohesion.activity.Erosion"
    )
    assert t.evidence["slope"] <= EnrichmentConfig().erosion_trend_max
    assert t.evidence["buckets"] >= 3
    assert t.evidence["window_weeks"] == 4


def test_erosion_skipped_on_flat_cadence():
    """Roughly constant per-window commit counts → no emit."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("er-flat")
    alice = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(alice)
    f = make_file("src/flat.py", project.ref())
    graph.files.add(f)
    # Five 4-week windows, each with exactly 4 commits — slope ~0.
    cid = 0
    for weeks_back in (20, 16, 12, 8, 4):
        for _ in range(4):
            cid += 1
            c = make_commit(f"e_{cid}", "feat", alice,
                            now - timedelta(weeks=weeks_back, days=cid),
                            project.ref())
            graph.commits.add(c)
            add_change(graph, c, f, added=5)
    _consume_metric(graph, AnomalyCohesionMetric())
    assert graph.traits.of_name("anomaly.cohesion.activity.Erosion") == ()


# ======================================================================
# Pipeline integration smoke
# ======================================================================
def test_pipeline_emits_chunk_15_traits_in_one_pass():
    """All three Chunk-15 metrics fire through ``run_pipeline``."""
    from src.enrichment.pipeline import run_pipeline
    now = datetime.now(UTC)
    graph, project = build_v2_graph("pipeline-smoke")
    alice = make_account("Alice", "a@x", project.ref())
    graph.git_accounts.add(alice)

    # RefactoringMagnet target — 12 refactor commits on one file.
    f_refactor = make_file("src/r.py", project.ref())
    graph.files.add(f_refactor)
    for i in range(12):
        c = make_commit(f"r_{i}", f"refactor: clean {i}", alice,
                        now - timedelta(days=80 - i * 5), project.ref())
        graph.commits.add(c)
        add_change(graph, c, f_refactor, added=5, deleted=5)

    # IdenticalFilenames target — 3 files sharing basename.
    for sub in ("a", "b", "c"):
        f = make_file(f"src/{sub}/util.py", project.ref())
        graph.files.add(f)

    res = run_pipeline(graph, EnrichmentConfig())
    assert res is not None
    refactor_traits = graph.traits.of_name("anomaly.testing.RefactoringMagnet")
    assert any(t.target == f_refactor.ref() for t in refactor_traits)
    idf_traits = graph.traits.of_name("anomaly.structuring.IdenticalFilenames")
    assert {t.target.id for t in idf_traits} >= {
        "src/a/util.py", "src/b/util.py", "src/c/util.py",
    }
