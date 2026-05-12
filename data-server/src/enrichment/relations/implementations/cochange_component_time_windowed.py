"""Cochange (component ↔ component, time-windowed) — DEFERRED stub.

Port of legacy
``src/enrichment/relations/cochange_component_time_windowed.py``. Depends
on the deferred ``cochange.component`` builder + the temporal index used
by ``cochange.file_time_windowed``.

See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class CochangeComponentTimeWindowedBuilder(RelationBuilder):
    name = "cochange.component_time_windowed"
    relation_kind = "cochange_component_time_windowed"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        raise NotImplementedError(
            "CochangeComponentTimeWindowedBuilder port deferred — depends on "
            "ComponentRegistry + temporal index. See handoff."
        )


__all__ = ["CochangeComponentTimeWindowedBuilder"]
