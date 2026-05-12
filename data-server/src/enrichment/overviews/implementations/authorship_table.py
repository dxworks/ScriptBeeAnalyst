"""Authorship overview — DEFERRED stub.

Port of legacy ``src/enrichment/overview/authorship_table.py``. Columns:
total_authors, active_authors, newcomer_ratio, senior_ratio,
bus_factor_1_files, dominant_author_share. Rows: synthetic
``(project)`` aggregate + one per top-level folder.
"""
from __future__ import annotations

from typing import ClassVar

from src.enrichment.overviews.models import OverviewTable, OverviewTableBuilder
from src.enrichment.overviews.registries import OVERVIEWS


@OVERVIEWS.register
class AuthorshipTableBuilder(OverviewTableBuilder):
    name: ClassVar[str] = "authorship"

    def build(self, graph, config) -> OverviewTable:
        raise NotImplementedError(
            "AuthorshipTableBuilder port deferred — depends on the knowledge "
            "anomaly + author classifier output. See Chunk 7 handoff."
        )


__all__ = ["AuthorshipTableBuilder"]
