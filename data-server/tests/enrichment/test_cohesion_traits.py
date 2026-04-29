"""Cohesion-family traits and SharedKnowledge emit on the cohesion fixture."""
from __future__ import annotations

from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import build_cohesion_graph


def _trait_names(entity_tags):
    return {t.name for t in entity_tags.traits}


def test_bazaar_emitted_on_many_authors_recent():
    e = compute_enrichments(build_cohesion_graph(), EnrichmentConfig())
    tags = e.tags_by_entity.get("file:src/bazaar.py")
    assert tags is not None
    assert "anomaly.cohesion.coordination.Bazaar" in _trait_names(tags)
    bz = next(t for t in tags.traits if t.name == "anomaly.cohesion.coordination.Bazaar")
    assert bz.evidence["distinct_authors_recent"] >= bz.evidence["threshold"]


def test_cathedral_emitted_on_dominant_recent_author():
    e = compute_enrichments(build_cohesion_graph(), EnrichmentConfig())
    tags = e.tags_by_entity.get("file:src/cathedral.py")
    assert tags is not None
    assert "anomaly.cohesion.coordination.Cathedral" in _trait_names(tags)
    cath = next(t for t in tags.traits if t.name == "anomaly.cohesion.coordination.Cathedral")
    assert cath.evidence["dominance_ratio"] >= cath.evidence["threshold"]


def test_pulsar_emitted_on_bursty_intervals():
    e = compute_enrichments(build_cohesion_graph(), EnrichmentConfig())
    tags = e.tags_by_entity.get("file:src/pulsar.py")
    assert tags is not None
    assert "anomaly.cohesion.coordination.Pulsar" in _trait_names(tags)
    p = next(t for t in tags.traits if t.name == "anomaly.cohesion.coordination.Pulsar")
    assert p.evidence["interval_cv"] >= p.evidence["threshold"]
    assert p.evidence["commits"] >= 6


def test_shared_knowledge_emitted_on_balanced_authors():
    """The bazaar file has 6 distinct authors with comparable churn — high entropy."""
    e = compute_enrichments(build_cohesion_graph(), EnrichmentConfig())
    tags = e.tags_by_entity.get("file:src/bazaar.py")
    assert tags is not None
    assert "anomaly.knowledge.SharedKnowledge" in _trait_names(tags)
    sk = next(t for t in tags.traits if t.name == "anomaly.knowledge.SharedKnowledge")
    assert sk.evidence["distinct_authors"] >= 3
    assert sk.evidence["entropy"] >= sk.evidence["threshold"]


def test_pulsar_min_intervals_threshold_is_config_driven():
    """Raising pulsar_min_intervals beyond the fixture's intervals suppresses Pulsar."""
    cfg = EnrichmentConfig(pulsar_min_intervals=999)
    e = compute_enrichments(build_cohesion_graph(), cfg)
    tags = e.tags_by_entity.get("file:src/pulsar.py")
    if tags is not None:
        assert "anomaly.cohesion.coordination.Pulsar" not in _trait_names(tags)
