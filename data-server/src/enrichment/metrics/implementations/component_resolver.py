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
should derive it from the relation set via the standalone
:func:`build_components_from_relations` helper exported below. The
helper is invoked by :func:`src.processor.build_graph_from_bundles`
right after :func:`src.enrichment.pipeline.run_pipeline` finishes — the
:class:`Metric` ABC forbids in-pipeline registry mutations, so the
component-registry population runs as a separate post-pipeline step.

Reads from the host: ``files`` (registry). Configurable via
``config.components_mapping_path``.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from typing import TYPE_CHECKING, Any, ClassVar, Iterable, List, Optional

from src.common.domains.components.models import Component
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


# ----------------------------------------------------------------------
# Post-pipeline registry population
# ----------------------------------------------------------------------
def build_components_from_relations(graph: "Graph") -> List[Component]:
    """Materialise :class:`Component` entities from ``component_membership`` relations.

    Walks ``graph.relations.of_kind("component_membership")`` (emitted
    earlier by :class:`ComponentResolverMetric`), groups file refs by
    component name, and inserts one :class:`Component` per unique name
    into ``graph.components``. Returns the list of inserted components
    (in stable insertion order) so callers can log / assert against it.

    For each component:

    * ``id`` / ``name`` — the component name (relation target id).
    * ``path_prefix`` — read from ``rel.extras["path_prefix"]`` (the
      metric already stamped it; no need to reconstruct the resolver).
    * ``file_refs`` — every file ref that resolved to this name, in
      first-seen order.
    * ``project_ref`` — the most common ``project_ref`` across the
      component's files (ties broken by first occurrence). ``None`` when
      no file resolves through ``graph`` (e.g. the file was removed
      between metric run and helper invocation).
    * ``description`` — ``None`` (reserved for a future curation field).

    Idempotent on a stable relation set: re-running replaces components
    in-place via :meth:`ComponentRegistry.add`.
    """
    # OrderedDict preserves first-seen order — useful for deterministic
    # output, log-scraping, and test assertions.
    by_name: "OrderedDict[str, List[EntityRef]]" = OrderedDict()
    prefix_by_name: dict[str, str] = {}

    for rel in graph.relations.of_kind("component_membership"):
        comp_ref = rel.target
        if comp_ref.kind != EntityKind.COMPONENT:
            continue
        name = comp_ref.id
        bucket = by_name.setdefault(name, [])
        # Skip exact-duplicate file refs (defensive — relation registry
        # already dedups on canonical id, but a re-run with the same data
        # would otherwise emit the same ref twice).
        if rel.source not in bucket:
            bucket.append(rel.source)
        # Last-writer wins on the prefix; in practice every relation for
        # a given component name carries the same prefix.
        prefix = rel.extras.get("path_prefix")
        if isinstance(prefix, str):
            prefix_by_name[name] = prefix

    built: List[Component] = []
    for name, file_refs in by_name.items():
        project_ref = _most_common_project_ref(graph, file_refs)
        component = Component(
            id=name,
            name=name,
            path_prefix=prefix_by_name.get(name, ""),
            file_refs=list(file_refs),
            project_ref=project_ref,
            description=None,
        )
        graph.components.add(component)
        built.append(component)
    return built


def _most_common_project_ref(
    graph: "Graph", file_refs: Iterable[EntityRef]
) -> Optional[EntityRef]:
    """Return the most frequent ``project_ref`` across the resolved files.

    Returns ``None`` when no file resolves through the graph or none of
    the resolved files carry a ``project_ref``. Ties resolve to the
    first-occurring project (Counter.most_common is stable on ties for
    the same count in insertion order in Python 3.7+).
    """
    counts: "Counter[EntityRef]" = Counter()
    for ref in file_refs:
        entity = graph.resolve(ref)
        if entity is None:
            continue
        project_ref = getattr(entity, "project_ref", None)
        if project_ref is None:
            continue
        counts[project_ref] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


__all__ = ["ComponentResolverMetric", "build_components_from_relations"]
