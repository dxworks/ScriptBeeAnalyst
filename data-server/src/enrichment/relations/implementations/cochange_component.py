"""Cochange (component ↔ component) builder — Chunk 14 port.

Aggregates the central :class:`CochangeBuilder` (``relation_kind="cochange"``)
output up to the component level by resolving each endpoint's file path
through :class:`ComponentResolver`. The Chunk-12 deferral landed here —
file-cochange now exists (Chunk 13), so we can lean on the established
file edges instead of re-deriving from changes.

Algorithm
---------

Per the §5 reuse map ("``ComponentResolverMetric`` is already
populated; consume it, don't re-derive"):

1. Read ``graph.relations.of_kind_in_window("cochange", LIFETIME)`` and
   the same for ``RECENT``.
2. Resolve each edge's ``source`` and ``target`` (both file refs) to
   component names via :class:`ComponentResolver`. Self-loops and
   un-resolvable endpoints are dropped.
3. Sum strengths per component pair; emit one :class:`Relation` per
   pair per window.

Why we re-construct ComponentResolver instead of reading
``graph.components`` (the registry)
-----------------------------------------------------------------

:class:`ComponentResolverMetric` runs in **stage 2** of
:func:`run_pipeline`; this builder runs in **stage 1**. So
``graph.components`` is empty and ``component_membership`` relations
don't exist when we fire. Constructing a resolver inline from
``config.components_mapping_path`` mirrors what the metric does — same
input → same output — at a cost of one regex sort per builder. See
``_component_aggregator.build_resolver`` for the shared construction
path.

Intra-stage ordering
--------------------

This builder runs AFTER :class:`CochangeBuilder` (file domain) because
imports in :mod:`src.enrichment.relations.implementations.__init__`
register the file-* variants before the component-* variants. If a
future change reshuffles registration order, the component-* builders
need to declare a dependency on ``cochange.file*``; no mechanism today.
See Chunk 13 review N2 and the Chunk-14 handoff for the same trap one
layer up.

Reads
-----

* ``graph.relations.of_kind("cochange")`` — aggregated file-file edges.
* ``graph.config.components_mapping_path`` — optional mapping JSON.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

from ._component_aggregator import aggregate_file_relations_to_components

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class CochangeComponentBuilder(RelationBuilder):
    """Component-domain rollup of the central file cochange relation."""

    name = "cochange.component"
    relation_kind = "cochange_component"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        yield from aggregate_file_relations_to_components(
            graph,
            source_kind="cochange",
            target_kind=self.relation_kind,
            extras_factory=lambda _pair, strength: {"strength": round(strength, 4)},
        )


__all__ = ["CochangeComponentBuilder"]
