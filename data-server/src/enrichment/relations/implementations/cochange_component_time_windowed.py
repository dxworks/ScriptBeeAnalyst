"""Cochange (component ↔ component, time-windowed) builder — Chunk 14 port.

Aggregates :class:`CochangeFileTimeWindowedBuilder`
(``relation_kind="cochange_file_time_windowed"``) up to the component
domain. Same shape as :class:`CochangeComponentBuilder` — only the
source-kind string differs.

Algorithm choice (a) "aggregate from file-cochange via component
membership" was selected over (b) "walk TemporalIndex directly,
mapping commits → component-sets via file changes" because:

* (a) automatically inherits the file builder's bulk-commit / merge /
  empty-changes filtering — re-deriving via TemporalIndex would
  duplicate that logic in the component layer.
* (a) reuses the canonical file-cochange edge set as the single source
  of truth; (b) would risk subtle divergence if the file builder's
  filtering rules evolve.
* The intra-stage ordering trap (file-* must register before
  component-*) is paid once in
  :mod:`src.enrichment.relations.implementations.__init__` and reused
  by all four component cochange variants.

(b) becomes preferable only if a future optimisation needs to compute
component-time-windowed without paying for the file-domain emission;
that is not the current case (file edges are computed unconditionally).

Reads
-----

* ``graph.relations.of_kind("cochange_file_time_windowed")``
* ``graph.config.components_mapping_path``
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

from ._component_aggregator import aggregate_file_relations_to_components

if TYPE_CHECKING:
    from src.common.kernel import Graph


_DEFAULT_TIME_WINDOWED_HOURS = 24


@BUILDERS.register
class CochangeComponentTimeWindowedBuilder(RelationBuilder):
    """Component-domain rollup of the time-windowed file-cochange edges."""

    name = "cochange.component_time_windowed"
    relation_kind = "cochange_component_time_windowed"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        hours = _config_field(
            graph, "time_windowed_cochange_hours", _DEFAULT_TIME_WINDOWED_HOURS
        )
        yield from aggregate_file_relations_to_components(
            graph,
            source_kind="cochange_file_time_windowed",
            target_kind=self.relation_kind,
            extras_factory=lambda _pair, strength: {
                "hours": float(hours) if hours is not None else 0.0,
                "strength": round(strength, 4),
            },
        )


def _config_field(graph, field, default):
    cfg = getattr(graph, "config", None)
    if cfg is None:
        return default
    return getattr(cfg, field, default)


__all__ = ["CochangeComponentTimeWindowedBuilder"]
