"""Components overview — DEFERRED stub.

Port of legacy ``src/enrichment/overview/components_table.py``. Depends
on :class:`ComponentResolverMetric` populating ``component_membership``
relations (Chunk 7 D) and on aggregate metrics over those membership
groups. See handoff.
"""
from __future__ import annotations

from typing import ClassVar

from src.enrichment.overviews.models import OverviewTable, OverviewTableBuilder
from src.enrichment.overviews.registries import OVERVIEWS


@OVERVIEWS.register
class ComponentsTableBuilder(OverviewTableBuilder):
    name: ClassVar[str] = "components"

    def build(self, graph, config) -> OverviewTable:
        raise NotImplementedError(
            "ComponentsTableBuilder port deferred — needs full component "
            "registry wiring. See Chunk 7 handoff."
        )


__all__ = ["ComponentsTableBuilder"]
