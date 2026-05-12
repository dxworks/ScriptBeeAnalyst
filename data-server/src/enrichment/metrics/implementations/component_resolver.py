"""Component resolver metric — emits file → component membership relations.

Port of legacy
``src/enrichment/components/{mapping,resolver}.py`` + the bit of
``compute_enrichments`` that builds the per-project component list.

Design choice
-------------

The legacy ``ComponentResolver`` produces :class:`Component` entities
(folder-grouped file lists). The v2 :class:`Metric` interface emits
:class:`Trait` / :class:`Classifier` / :class:`Relation` — NOT entities.
To stay in-contract we emit ``component_membership`` :class:`Relation`
rows::

    Relation(source=file_ref, target=<synthetic component ref>,
             relation_kind="component_membership", ...)

The synthetic component :class:`EntityRef` uses ``kind=EntityKind.COMPONENT``
and ``id=<component_name>``. Downstream code that needs the
:class:`Component` entity itself (with path_prefix + file_refs rollup)
should derive it from the relation set or call the standalone
:func:`build_components_from_relations` helper (this module). Chunk 8
will wire a real :class:`ComponentRegistry` populated from the relations
the metric emits.

Reads from the host: ``files`` (registry). Configurable via
``config.components_mapping_path``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.domains.components.resolver import (
    ComponentMapping,
    ComponentResolver,
    load_component_mapping,
)
from src.common.kernel import EntityKind, EntityRef
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.relations import Relation, WindowKind

if TYPE_CHECKING:
    from src.common.kernel import Graph


@METRICS.register
class ComponentResolverMetric(Metric):
    """Emits a ``component_membership`` :class:`Relation` per file."""

    name: ClassVar[str] = "component.resolver"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_relations=["component_membership"]
    )
    config_fields: ClassVar[list[str]] = ["components_mapping_path"]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Relation]:
        files = _safe_iter(getattr(graph, "files", None))
        if not files:
            return

        mapping_path = _config_field(config, "components_mapping_path", None)
        mapping = load_component_mapping(mapping_path)
        resolver = ComponentResolver(mapping)

        for file_ in files:
            file_ref = file_.ref()
            comp_name = resolver.resolve(file_.id)
            if comp_name is None:
                continue
            comp_ref = EntityRef(kind=EntityKind.COMPONENT, id=comp_name)
            rid = Relation.canonical_id(
                file_ref, comp_ref, "component_membership", WindowKind.LIFETIME
            )
            yield Relation(
                id=rid,
                source=file_ref,
                target=comp_ref,
                relation_kind="component_membership",
                window=WindowKind.LIFETIME,
                strength=1.0,
                extras={"path_prefix": resolver.prefix_for(comp_name)},
            )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_iter(reg: Any) -> list[Any]:
    if reg is None:
        return []
    try:
        return list(reg)
    except TypeError:
        return []


def _config_field(config: Any, field: str, default: Any) -> Any:
    if config is None:
        return default
    return getattr(config, field, default)


__all__ = ["ComponentResolverMetric"]
