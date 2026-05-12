"""Cochange (component ↔ component) — DEFERRED stub.

Port of legacy ``src/enrichment/relations/cochange_component.py``. The
legacy aggregates file-file cochange edges up to the component level
using the :class:`ComponentResolver` mapping. The v2 port depends on
:mod:`src.common.domains.components` (this chunk D) being wired into
the host. Deferred until the host carries a :class:`ComponentRegistry`
populated by :class:`ComponentResolverMetric`.

See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations_v2 import Relation, RelationBuilder, WindowKind
from src.enrichment.relations_v2.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class CochangeComponentBuilder(RelationBuilder):
    name = "cochange.component"
    relation_kind = "cochange_component"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        raise NotImplementedError(
            "CochangeComponentBuilder port deferred — needs ComponentRegistry "
            "wired on the host. See Chunk 7 handoff 'Deferred ports'."
        )


__all__ = ["CochangeComponentBuilder"]
