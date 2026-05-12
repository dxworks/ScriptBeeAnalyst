"""Skeletal overview models (NOT graph entities).

Per plan §13 / Chunk 7 brief §E. The three Pydantic models below mirror
the legacy ``src/enrichment/models.py`` overview shapes; the
:class:`OverviewTableBuilder` ABC is the Chunk-7 surface that each
``overview/*_table.py`` will port to.

These are NOT :class:`Entity` subclasses — overviews are presentational
rollups, not addressable graph nodes. They live in
:class:`OverviewTableRegistry` (a plain catalog, see :mod:`.registries`).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from src.common.kernel import Graph


# Cell-value union — mirror of legacy ``models.CellValue``. Kept typed
# (no ``Any``) consistent with the chunk-3 "typed evidence" discipline.
CellValue = Union[float, int, str, None]
HighlightKind = Union[str, None]  # "none" / "good" / "warn" / "bad"


class OverviewCell(BaseModel):
    """One cell in an overview table.

    Mirrors legacy dx's ``lifetime | recent | trend%`` triple. The
    optional ``highlight`` carries a quality colour for the UI ("warn",
    "bad", ...).
    """

    model_config = ConfigDict(extra="forbid")

    lifetime_value: CellValue = None
    recent_value: CellValue = None
    trend_percent: Optional[float] = None
    highlight: HighlightKind = None


class OverviewRow(BaseModel):
    """One row in an overview table — keyed on a stable id (file path / component name).

    ``cells`` is a column-name → :class:`OverviewCell` dict.
    """

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    cells: dict[str, OverviewCell] = Field(default_factory=dict)


class OverviewTable(BaseModel):
    """One overview table — name + columns + rows.

    ``name`` is the registry key (one per builder). ``entity_kind`` is a
    legacy-DX label describing what the rows represent (``"file"`` /
    ``"component"`` / ``"author"`` / ``"issue"`` / ``"pr"``); kept as a
    plain string for backwards compatibility with the legacy DX UI.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    entity_kind: str
    columns: list[str] = Field(default_factory=list)
    rows: list[OverviewRow] = Field(default_factory=list)


class OverviewTableBuilder(ABC):
    """Pluggable producer of :class:`OverviewTable` instances.

    Subclass contract::

        @OVERVIEWS.register
        class AuthorshipTableBuilder(OverviewTableBuilder):
            name: ClassVar[str] = "authorship"

            def build(self, graph, config) -> OverviewTable:
                ...

    The :class:`OverviewTableRegistry` (in :mod:`.registries`) iterates
    every registered builder; Chunk 8 wires this into a pipeline pass
    that runs AFTER metrics + builders (since most overview tables read
    trait / classifier output).
    """

    name: ClassVar[str]

    @abstractmethod
    def build(self, graph: "Graph", config: object) -> OverviewTable:
        """Produce the overview table for the current graph state.

        Returns a fully-materialised :class:`OverviewTable`. Unlike
        metrics / builders, overview construction is one table per
        builder — the registry stores one table per name.
        """


__all__ = [
    "CellValue",
    "HighlightKind",
    "OverviewCell",
    "OverviewRow",
    "OverviewTable",
    "OverviewTableBuilder",
]
