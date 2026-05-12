"""Pace overview — DEFERRED stub.

Port of legacy ``src/enrichment/overview/pace_table.py``. Depends on
commit timing rollups (daytime + weekday + commit cadence). See handoff.
"""
from __future__ import annotations

from typing import ClassVar

from src.enrichment.overviews.models import OverviewTable, OverviewTableBuilder
from src.enrichment.overviews.registries import OVERVIEWS


@OVERVIEWS.register
class PaceTableBuilder(OverviewTableBuilder):
    name: ClassVar[str] = "pace"

    def build(self, graph, config) -> OverviewTable:
        raise NotImplementedError(
            "PaceTableBuilder port deferred. See Chunk 7 handoff."
        )


__all__ = ["PaceTableBuilder"]
