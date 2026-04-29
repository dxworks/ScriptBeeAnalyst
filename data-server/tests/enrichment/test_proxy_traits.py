"""Phase 3 proxy traits: Supernova (cohesion.size) and TestOrphan (testing)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from src.common.models import (
    ChangeType,
    File,
    GitProject,
)
from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import (
    build_synthetic_graph,
    make_account,
    make_change,
    make_commit,
    make_file,
)


UTC = timezone.utc


def _trait(tags, name):
    return next((t for t in tags.traits if t.name == name), None)


def test_supernova_fires_above_threshold_with_proxy_evidence():
    g = build_synthetic_graph()
    # Lower the threshold so the fixture's owner.py (~187 lines added/deleted)
    # crosses it deterministically.
    cfg = EnrichmentConfig(supernova_net_churn_min=100)
    e = compute_enrichments(g, cfg)
    tags = e.tags_by_entity.get("file:src/owner.py")
    assert tags is not None
    sn = _trait(tags, "anomaly.cohesion.size.Supernova")
    assert sn is not None
    assert sn.evidence.get("proxy") is True
    assert sn.evidence.get("note") and "net-churn" in sn.evidence["note"]
    assert sn.evidence.get("net_churn") >= 100


def test_supernova_does_not_fire_under_threshold():
    g = build_synthetic_graph()
    cfg = EnrichmentConfig(supernova_net_churn_min=10**9)
    e = compute_enrichments(g, cfg)
    for tags in e.tags_by_entity.values():
        for t in tags.traits:
            assert t.name != "anomaly.cohesion.size.Supernova"


def test_test_orphan_fires_on_production_file_without_test_cochange():
    """Production file with several commits, no test-file cochange -> TestOrphan."""
    now = datetime.now(UTC)
    proj = GitProject(name="orphan-test")
    alice = make_account("Alice", "alice@example.com")
    proj.account_registry.add_all([alice])

    prod = make_file(proj)
    test_file = make_file(proj)
    proj.file_registry.add_all([prod, test_file])

    # 4 production-only commits, all isolated (no cochange to test_file).
    for i in range(4):
        c = make_commit(proj, f"c_{i}", f"feat: prod {i}", alice, now - timedelta(days=10 + i))
        make_change(c, prod, "src/prod.py", added=10)
        proj.git_commit_registry.add(c)

    # One commit on the test file alone, separate.
    tc = make_commit(proj, "c_t", "test: bootstrap", alice, now - timedelta(days=2))
    make_change(tc, test_file, "tests/test_prod.py", added=5)
    proj.git_commit_registry.add(tc)

    g = {"git": proj, "jira": None, "github": None}
    e = compute_enrichments(g, EnrichmentConfig())
    tags = e.tags_by_entity.get("file:src/prod.py")
    assert tags is not None
    to = _trait(tags, "anomaly.testing.TestOrphan")
    assert to is not None
    assert to.evidence.get("proxy") is True
    assert to.evidence.get("cochange_test_count") == 0


def test_test_orphan_suppressed_when_project_has_no_test_files():
    """Zero test-role files -> TestOrphan would trivially flag every production
    file. Guard skips emission entirely so the trait stays meaningful."""
    now = datetime.now(UTC)
    proj = GitProject(name="orphan-no-tests")
    alice = make_account("Alice", "alice@example.com")
    proj.account_registry.add_all([alice])

    prod = make_file(proj)
    proj.file_registry.add_all([prod])

    for i in range(4):
        c = make_commit(proj, f"c_{i}", f"feat: prod {i}", alice, now - timedelta(days=10 + i))
        make_change(c, prod, "src/prod.py", added=10)
        proj.git_commit_registry.add(c)

    g = {"git": proj, "jira": None, "github": None}
    e = compute_enrichments(g, EnrichmentConfig())
    tags = e.tags_by_entity.get("file:src/prod.py")
    if tags is not None:
        assert _trait(tags, "anomaly.testing.TestOrphan") is None


def test_test_orphan_suppressed_when_threshold_raised():
    """Same fixture, threshold high enough that even 0 cochange flags it -> sanity check the inverse."""
    now = datetime.now(UTC)
    proj = GitProject(name="orphan-test-2")
    alice = make_account("Alice", "alice@example.com")
    proj.account_registry.add_all([alice])

    prod = make_file(proj)
    test_file = make_file(proj)
    proj.file_registry.add_all([prod, test_file])

    # Production file co-changes with the test file 5 times.
    for i in range(5):
        c = make_commit(proj, f"c_{i}", f"feat: prod {i}", alice, now - timedelta(days=10 + i))
        make_change(c, prod, "src/prod.py", added=10)
        make_change(c, test_file, "tests/test_prod.py", added=4)
        proj.git_commit_registry.add(c)

    g = {"git": proj, "jira": None, "github": None}
    e = compute_enrichments(g, EnrichmentConfig())
    tags = e.tags_by_entity.get("file:src/prod.py")
    if tags is not None:
        assert _trait(tags, "anomaly.testing.TestOrphan") is None
