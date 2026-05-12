"""Feature-encapsulation overview — DEFERRED stub.

Port of legacy
``src/enrichment/overview/feature_encapsulation_table.py``. Depends on
commit classifiers (volume.churn / volume.spread) + cross-source
relations. See handoff.
"""
from __future__ import annotations

from typing import ClassVar

from src.enrichment.overviews.models import OverviewTable, OverviewTableBuilder
from src.enrichment.overviews.registries import OVERVIEWS


@OVERVIEWS.register
class FeatureEncapsulationTableBuilder(OverviewTableBuilder):
    name: ClassVar[str] = "feature_encapsulation"

    def build(self, graph, config) -> OverviewTable:
        raise NotImplementedError(
            "FeatureEncapsulationTableBuilder port deferred. See Chunk 7 handoff."
        )


__all__ = ["FeatureEncapsulationTableBuilder"]
