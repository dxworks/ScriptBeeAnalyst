"""Code-quality overview — DEFERRED stub.

Port of legacy ``src/enrichment/overview/code_quality_table.py``.
Depends on the v2 ``QualityIssueRegistry`` + Lizard FileMetric data
being wired (Chunk 8). See handoff.
"""
from __future__ import annotations

from typing import ClassVar

from src.enrichment.overviews.models import OverviewTable, OverviewTableBuilder
from src.enrichment.overviews.registries import OVERVIEWS


@OVERVIEWS.register
class CodeQualityTableBuilder(OverviewTableBuilder):
    name: ClassVar[str] = "code_quality"

    def build(self, graph, config) -> OverviewTable:
        raise NotImplementedError(
            "CodeQualityTableBuilder port deferred. See Chunk 7 handoff."
        )


__all__ = ["CodeQualityTableBuilder"]
