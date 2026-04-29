"""Anomaly trait emission on the synthetic fixture."""
from __future__ import annotations

from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import (
    build_github_fixture,
    build_jira_fixture,
    build_synthetic_graph,
)


def _trait_names(entity_tags):
    return {t.name for t in entity_tags.traits}


def _enrich(cfg=None):
    g = build_synthetic_graph()
    g["jira"] = build_jira_fixture()
    g["github"] = build_github_fixture()
    return compute_enrichments(g, cfg or EnrichmentConfig())


def test_bugmagnet_emitted_on_buggy_file():
    e = _enrich()
    tags = e.tags_by_entity.get("file:src/buggy.py")
    assert tags is not None
    assert "anomaly.testing.BugMagnet" in _trait_names(tags)
    bm = next(t for t in tags.traits if t.name == "anomaly.testing.BugMagnet")
    assert bm.evidence["bugfix_commits"] == 5
    assert bm.evidence["bugfix_ratio"] >= 0.4


def test_busfactor1_emitted_on_owner_file():
    e = _enrich()
    tags = e.tags_by_entity.get("file:src/owner.py")
    assert tags is not None
    assert "anomaly.knowledge.BusFactor1" in _trait_names(tags)


def test_orphan_emitted_on_orphan_file():
    e = _enrich()
    tags = e.tags_by_entity.get("file:src/orphan.py")
    assert tags is not None
    assert "anomaly.knowledge.Orphan" in _trait_names(tags)


def test_tasks_bottleneck_emitted_on_old_open_issue():
    e = _enrich()
    tags = e.tags_by_entity.get("issue:PROJ-1")
    assert tags is not None
    names = _trait_names(tags)
    assert "anomaly.structuring.TasksBottleneck" in names


def test_threshold_overrides_change_emission():
    """Raising the bugmagnet ratio above the file's value must suppress it."""
    cfg = EnrichmentConfig(bugmagnet_ratio_min=0.99)
    e = _enrich(cfg)
    tags = e.tags_by_entity.get("file:src/buggy.py")
    if tags is not None:
        assert "anomaly.testing.BugMagnet" not in _trait_names(tags)
