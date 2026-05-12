"""Cochange (author ↔ author, shared-task-prefixes) — DEFERRED stub.

Port of legacy
``src/enrichment/relations/cochange_author_shared_task_prefixes.py``.
Depends on a typed commit-message task-prefix indexer.

See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class CochangeAuthorSharedTaskPrefixesBuilder(RelationBuilder):
    name = "cochange.author_shared_task_prefixes"
    relation_kind = "cochange_author_shared_task_prefixes"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        raise NotImplementedError(
            "CochangeAuthorSharedTaskPrefixesBuilder port deferred — depends on "
            "commit-message task-prefix indexer. See handoff."
        )


__all__ = ["CochangeAuthorSharedTaskPrefixesBuilder"]
