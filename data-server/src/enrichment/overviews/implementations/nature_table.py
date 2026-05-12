"""Nature overview — DEFERRED stub.

Port of legacy ``src/enrichment/overview/nature_table.py``. Depends on
the commit classifier ``message.nature`` output (which Chunk-7's
:class:`CommitClassifierMetric` substantively ships). The overview
rollup itself is deferred to keep this chunk's surface small. See
handoff.
"""
from __future__ import annotations

from typing import ClassVar

from src.enrichment.overviews.models import OverviewTable, OverviewTableBuilder
from src.enrichment.overviews.registries import OVERVIEWS


@OVERVIEWS.register
class NatureTableBuilder(OverviewTableBuilder):
    name: ClassVar[str] = "nature"

    def build(self, graph, config) -> OverviewTable:
        raise NotImplementedError(
            "NatureTableBuilder port deferred. See Chunk 7 handoff."
        )


__all__ = ["NatureTableBuilder"]
