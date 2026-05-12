"""File ↔ File inheritance/interface builder.

Port of legacy ``src/enrichment/relations/hierarchy.py``. Source:
:class:`CodeReference` rows whose ``reference_kind`` is ``"inheritance"``
or ``"interface"``. Strength = sum of ``weight``. Self-loops dropped.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations_v2 import Relation, RelationBuilder, WindowKind
from src.enrichment.relations_v2.builders import BUILDERS
from .calls import _emit_typed_refs

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class HierarchyBuilder(RelationBuilder):
    name = "hierarchy"
    relation_kind = "hierarchy"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        yield from _emit_typed_refs(
            graph,
            self.relation_kind,
            allowed_reference_kinds={"inheritance", "interface"},
        )


__all__ = ["HierarchyBuilder"]
