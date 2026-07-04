"""Cochange (component ↔ component, shared-task-prefixes) builder — Chunk 14 port.

Aggregates :class:`CochangeFileSharedTaskPrefixesBuilder`
(``relation_kind="cochange_file_shared_task_prefixes"``) up to the
component domain.

Algorithm
---------

Same shape as the sibling component-cochange variants: read the
file-cochange edges, resolve endpoints to component names, sum
strengths per component pair.

Reads
-----

* ``graph.relations.of_kind("cochange_file_shared_task_prefixes")``
* ``graph.config.components_mapping_path``
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

from ._component_aggregator import aggregate_file_relations_to_components

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class CochangeComponentSharedTaskPrefixesBuilder(RelationBuilder):
    """Component-domain rollup of file shared-task-prefixes cochange edges."""

    name = "cochange.component_shared_task_prefixes"
    relation_kind = "cochange_component_shared_task_prefixes"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        yield from aggregate_file_relations_to_components(
            graph,
            source_kind="cochange_file_shared_task_prefixes",
            target_kind=self.relation_kind,
            extras_factory=lambda _pair, strength: {
                "shared_task_prefixes_strength": round(strength, 4),
            },
        )


__all__ = ["CochangeComponentSharedTaskPrefixesBuilder"]
