"""Cochange (component ↔ component, shared-devs) — DEFERRED stub.

Port of legacy
``src/enrichment/relations/cochange_component_shared_devs.py``. Depends
on the deferred ``cochange.component`` builder + a per-component author
set. See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class CochangeComponentSharedDevsBuilder(RelationBuilder):
    name = "cochange.component_shared_devs"
    relation_kind = "cochange_component_shared_devs"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        raise NotImplementedError(
            "CochangeComponentSharedDevsBuilder port deferred — depends on "
            "ComponentRegistry + per-component author index. See handoff."
        )


__all__ = ["CochangeComponentSharedDevsBuilder"]
