"""Overview tables — per-table rollups for the web UI.

See plan §13. Each overview table is a NAME → (rows × cells) matrix; the
plan keeps them out of the entity registries because they're a view, not
a graph node. The :class:`OverviewTableRegistry` is therefore a plain
catalog (mirrors :class:`MetricRegistry`), NOT a kernel
:class:`Registry`. Chunk 8 slots this into :class:`Graph` as a typed
field.

Implementations are under :mod:`.implementations`; importing the package
side-loads all 10 builders (one per legacy ``overview/*_table.py``). Most
are NotImplementedError stubs for chunk 7 — see handoff §"Components and
overviews".
"""
from __future__ import annotations

from .models import OverviewCell, OverviewRow, OverviewTable, OverviewTableBuilder
from .registries import OVERVIEWS, OverviewTableRegistry

__all__ = [
    "OVERVIEWS",
    "OverviewCell",
    "OverviewRow",
    "OverviewTable",
    "OverviewTableBuilder",
    "OverviewTableRegistry",
]
