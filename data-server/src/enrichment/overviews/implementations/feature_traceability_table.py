"""Feature-traceability overview — DEFERRED stub.

Port of legacy
``src/enrichment/overview/feature_traceability_table.py``. Depends on
issue ↔ file and pr ↔ file relations (Chunk 7 builders ship these). See
handoff.
"""
from __future__ import annotations

from typing import ClassVar

from src.enrichment.overviews.models import OverviewTable, OverviewTableBuilder
from src.enrichment.overviews.registries import OVERVIEWS


@OVERVIEWS.register
class FeatureTraceabilityTableBuilder(OverviewTableBuilder):
    name: ClassVar[str] = "feature_traceability"

    def build(self, graph, config) -> OverviewTable:
        raise NotImplementedError(
            "FeatureTraceabilityTableBuilder port deferred. See Chunk 7 handoff."
        )


__all__ = ["FeatureTraceabilityTableBuilder"]
