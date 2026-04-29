"""PivotFile trait emits on a hub-and-spoke co-change graph."""
from __future__ import annotations

from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import build_pivot_graph


def _trait_names(entity_tags):
    return {t.name for t in entity_tags.traits}


def test_pivot_file_trait_emitted_on_hub_file():
    e = compute_enrichments(build_pivot_graph(), EnrichmentConfig())
    tags = e.tags_by_entity.get("file:src/hub.py")
    assert tags is not None
    assert "anomaly.structuring.PivotFile" in _trait_names(tags)
    pivot = next(t for t in tags.traits if t.name == "anomaly.structuring.PivotFile")
    assert pivot.evidence["cochange_degree"] >= pivot.evidence["threshold"]


def test_pivot_file_threshold_override_suppresses():
    cfg = EnrichmentConfig(pivotfile_cochange_degree_min=100)
    e = compute_enrichments(build_pivot_graph(), cfg)
    tags = e.tags_by_entity.get("file:src/hub.py")
    if tags is not None:
        assert "anomaly.structuring.PivotFile" not in _trait_names(tags)
