"""File ↔ File data-access (field read/write) builder.

Port of legacy ``src/enrichment/relations/data_access.py``. Source:
:class:`CodeReference` rows whose ``reference_kind`` is ``"field_read"``
or ``"field_write"`` (the v2 model splits the legacy ``"fieldAccess"``).
Strength = sum of ``weight``. Self-loops dropped.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS
from .calls import _emit_typed_refs

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class DataAccessBuilder(RelationBuilder):
    name = "data_access"
    relation_kind = "data_access"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        # v2 CodeReference splits the legacy "fieldAccess" into two:
        # "field_read" + "field_write". Either contributes to data-access
        # coupling.
        yield from _emit_typed_refs(
            graph,
            self.relation_kind,
            allowed_reference_kinds={"field_read", "field_write"},
        )


__all__ = ["DataAccessBuilder"]
