"""Cochange (component ↔ component, shared-devs) builder — Chunk 14 port.

Aggregates :class:`CochangeFileSharedDevsBuilder`
(``relation_kind="cochange_file_shared_devs"``) up to the component
domain. The file-* builder has already done the hard work of computing
per-file author intersections and gating by actual cochange; this layer
is a straight component rollup.

Algorithm
---------

Per the §5 reuse map ("read aggregated cochange relations mapped
through component_membership, not re-derive from changes"):

1. Read ``cochange_file_shared_devs`` edges per window from
   ``graph.relations.of_kind_in_window(...)``.
2. Resolve each endpoint to a component name via
   :class:`ComponentResolver`. Drop self-loops + un-resolvable pairs.
3. Sum strengths per component pair.

The decision to chain off file-shared-devs (rather than a fresh
ownership + cochange.component pair-walk) matches the legacy
``ComponentSharedDevsCoChangeExtractor`` — same single source of truth.

Reads
-----

* ``graph.relations.of_kind("cochange_file_shared_devs")``
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
class CochangeComponentSharedDevsBuilder(RelationBuilder):
    """Component-domain rollup of file shared-devs cochange edges."""

    name = "cochange.component_shared_devs"
    relation_kind = "cochange_component_shared_devs"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        yield from aggregate_file_relations_to_components(
            graph,
            source_kind="cochange_file_shared_devs",
            target_kind=self.relation_kind,
            extras_factory=lambda _pair, strength: {
                "shared_devs_strength": round(strength, 4),
            },
        )


__all__ = ["CochangeComponentSharedDevsBuilder"]
