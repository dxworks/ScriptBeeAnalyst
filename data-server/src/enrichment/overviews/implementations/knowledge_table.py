"""Knowledge overview — DEFERRED stub.

Port of legacy ``src/enrichment/overview/knowledge_table.py``. Depends
on the knowledge anomaly tagger output (Orphan / BusFactor1 / etc.).
See handoff.
"""
from __future__ import annotations

from typing import ClassVar

from src.enrichment.overviews.models import OverviewTable, OverviewTableBuilder
from src.enrichment.overviews.registries import OVERVIEWS


@OVERVIEWS.register
class KnowledgeTableBuilder(OverviewTableBuilder):
    name: ClassVar[str] = "knowledge"

    def build(self, graph, config) -> OverviewTable:
        raise NotImplementedError(
            "KnowledgeTableBuilder port deferred. See Chunk 7 handoff."
        )


__all__ = ["KnowledgeTableBuilder"]
