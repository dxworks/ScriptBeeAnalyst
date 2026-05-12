"""Testing overview — DEFERRED stub.

Port of legacy ``src/enrichment/overview/testing_table.py``. Depends on
file-role classifier output + testing anomaly traits + per-file test
cochange relations. See handoff.
"""
from __future__ import annotations

from typing import ClassVar

from src.enrichment.overviews.models import OverviewTable, OverviewTableBuilder
from src.enrichment.overviews.registries import OVERVIEWS


@OVERVIEWS.register
class TestingTableBuilder(OverviewTableBuilder):
    name: ClassVar[str] = "testing"

    def build(self, graph, config) -> OverviewTable:
        raise NotImplementedError(
            "TestingTableBuilder port deferred. See Chunk 7 handoff."
        )


__all__ = ["TestingTableBuilder"]
