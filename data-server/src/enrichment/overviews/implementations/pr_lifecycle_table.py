"""PR-lifecycle overview — DEFERRED stub.

Port of legacy ``src/enrichment/overview/pr_lifecycle_table.py``. Depends
on PR classifiers (size / state / review_intensity) — the
:class:`IssuePRClassifierMetric` port itself is deferred. See handoff.
"""
from __future__ import annotations

from typing import ClassVar

from src.enrichment.overviews.models import OverviewTable, OverviewTableBuilder
from src.enrichment.overviews.registries import OVERVIEWS


@OVERVIEWS.register
class PrLifecycleTableBuilder(OverviewTableBuilder):
    name: ClassVar[str] = "pr_lifecycle"

    def build(self, graph, config) -> OverviewTable:
        raise NotImplementedError(
            "PrLifecycleTableBuilder port deferred. See Chunk 7 handoff."
        )


__all__ = ["PrLifecycleTableBuilder"]
