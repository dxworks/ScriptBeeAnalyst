"""Cochange (component ↔ component, shared-task-prefixes) — DEFERRED stub.

Port of legacy
``src/enrichment/relations/cochange_component_shared_task_prefixes.py``.
Depends on the deferred ``cochange.component`` builder + a typed
commit-message task-prefix indexer.

See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class CochangeComponentSharedTaskPrefixesBuilder(RelationBuilder):
    name = "cochange.component_shared_task_prefixes"
    relation_kind = "cochange_component_shared_task_prefixes"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        raise NotImplementedError(
            "CochangeComponentSharedTaskPrefixesBuilder port deferred — depends "
            "on ComponentRegistry + commit-message task-prefix indexer. See "
            "handoff."
        )


__all__ = ["CochangeComponentSharedTaskPrefixesBuilder"]
