"""Intent-impact overview — DEFERRED stub.

Port of legacy ``src/enrichment/overview/intent_impact_table.py``. Depends
on commit classifiers + churn rollups. See handoff.
"""
from __future__ import annotations

from typing import ClassVar

from src.enrichment.overviews.models import OverviewTable, OverviewTableBuilder
from src.enrichment.overviews.registries import OVERVIEWS


@OVERVIEWS.register
class IntentImpactTableBuilder(OverviewTableBuilder):
    name: ClassVar[str] = "intent_impact"

    def build(self, graph, config) -> OverviewTable:
        raise NotImplementedError(
            "IntentImpactTableBuilder port deferred. See Chunk 7 handoff."
        )


__all__ = ["IntentImpactTableBuilder"]
