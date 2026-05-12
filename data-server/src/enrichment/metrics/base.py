"""Metric ABC + ``MetricInputs`` + ``MetricOutputs``.

See §7 of ``architectural_changes.md``. A :class:`Metric` is the v2
analogue of today's per-tagger / per-relation-builder modules: a single
plugin that walks the graph and emits :class:`Trait` / :class:`Classifier`
/ :class:`Relation` entities.

A :class:`Metric` is **not** a graph entity (no ``EntityKind`` member). It
is catalog metadata + code. The :class:`MetricRegistry` in
:mod:`.registry` is therefore a plain catalog, NOT a ``Registry[Entity]``.
See the Chunk-3 handoff for the rationale.

Recipe (plan §10)::

    @METRICS.register
    class KnowledgeOrphan(Metric):
        name: ClassVar[str] = "knowledge.orphan"
        inputs:  ClassVar[MetricInputs] = MetricInputs(
            source_kind=EntityKind.FILE,
        )
        outputs: ClassVar[MetricOutputs] = MetricOutputs(
            emits_traits=["anomaly.knowledge.Orphan"],
        )
        config_fields: ClassVar[list[str]] = ["orphan_inactive_days"]

        def compute(self, graph, config):
            cutoff = recent_cutoff(...)
            for file in graph.files:
                if is_orphan(file, cutoff, config):
                    yield Trait(
                        id=...,
                        target=file.ref(),
                        family=TraitFamily.KNOWLEDGE,
                        name="anomaly.knowledge.Orphan",
                        ...
                    )
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Optional, Union

from pydantic import BaseModel, ConfigDict

from src.common.kernel import EntityKind

if TYPE_CHECKING:  # forward-only: avoid circular imports at module load
    from src.common.kernel import Graph
    from src.enrichment.relations_v2 import Relation
    from src.enrichment.tags import Classifier, Trait


class MetricInputs(BaseModel):
    """What the metric reads from the graph.

    Per plan §7. The three fields describe one of two valid input shapes:

    1. **Relation-shaped** — set ``relation_kind`` to operate over a kind
       of relation already present in ``graph.relations`` (e.g. a metric
       that scores cochange edges).
    2. **Pair-shaped** — set ``source_kind`` and/or ``target_kind`` to
       describe the entity-kind shape the metric walks (e.g. a metric over
       ``FILE → COMMIT`` pairs that derives its own edges).

    All three are :class:`Optional` because a metric may walk a single
    registry (e.g. ``source_kind=FILE`` only) or the whole graph.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    relation_kind: Optional[str] = None
    source_kind:   Optional[EntityKind] = None
    target_kind:   Optional[EntityKind] = None


class MetricOutputs(BaseModel):
    """What the metric writes back to the graph.

    Per plan §7. Each list enumerates the *names* of the artefacts the
    metric can emit (not the artefact instances). The enrichment pipeline
    (Chunk 7) reads these to:

    * route outputs into the right registry (trait → :class:`TraitRegistry`,
      etc.);
    * surface the metric in ``list_metrics`` (MCP); and
    * power the threshold-editor UI (task 2 — ``MetricOutputs.emits_*``
      together with :attr:`Metric.config_fields` is the single source of
      truth for "what does this metric do and what knobs does it expose").

    Defaults are empty lists — a metric that emits only traits leaves
    ``emits_classifiers``, ``emits_relations``, and
    ``emits_overview_columns`` empty.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    emits_traits:            list[str] = []
    emits_classifiers:       list[str] = []
    emits_relations:         list[str] = []
    emits_overview_columns:  list[str] = []


# ----------------------------------------------------------------------
# Metric ABC
# ----------------------------------------------------------------------

# Output union — typed (no Any). ``Iterable`` not ``list`` because metrics
# may stream large outputs; the pipeline materialises as needed.
MetricOutput = Union["Trait", "Classifier", "Relation"]


class Metric(ABC):
    """Pluggable graph analytic.

    Subclass contract:

    * ``name`` — unique catalog id, used by ``MetricRegistry.get(name)``.
    * ``inputs`` — :class:`MetricInputs` declaration (immutable).
    * ``outputs`` — :class:`MetricOutputs` declaration (immutable).
    * ``config_fields`` — names of threshold fields the metric reads from
      the supplied config. Defaults to empty.
    * ``compute(graph, config) -> Iterable[Trait | Classifier | Relation]``
      — the actual analytic. The runtime ``config`` type is intentionally
      left as ``Any`` for chunk-3: the legacy
      ``src/enrichment/config.py:EnrichmentConfig`` is a dataclass that
      Chunk 7 will replace with a typed shape derived from
      ``Metric.config_fields`` (per plan §10 step 3). For now downstream
      authors should annotate ``config: EnrichmentConfig`` in their own
      subclasses; we keep the base interface permissive so the
      not-yet-replaced legacy config doesn't constrain it.
    """

    name:           ClassVar[str]
    inputs:         ClassVar[MetricInputs]
    outputs:        ClassVar[MetricOutputs]
    config_fields:  ClassVar[list[str]] = []

    @abstractmethod
    def compute(self, graph: "Graph", config: Any) -> Iterable[MetricOutput]:
        """Walk ``graph`` and yield tag / relation entities.

        ``config`` is the legacy ``EnrichmentConfig`` (or any successor in
        Chunk 7). The pipeline calls this once per metric per pipeline
        run.

        **Purity contract.** A ``compute`` implementation MUST be a pure
        function of ``(graph, config)``: it must NOT mutate the graph or
        any of its registries (no ``graph.traits.add(...)``,
        ``graph.relations.add(...)``, etc.) and must NOT touch external
        state. The pipeline owns registry mutations: it iterates a
        metric's output stream and routes each :class:`Trait` /
        :class:`Classifier` / :class:`Relation` to the right registry by
        ``isinstance`` check. Two metrics emitting the same entity id
        collapse naturally on ``Registry.add`` (last writer wins; for
        :class:`Relation` use :meth:`Relation.canonical_id` so dedup is
        deterministic).

        ``compute`` may be a generator (``yield``) or return an iterable —
        the pipeline never assumes a materialised collection so large
        metrics can stream their output.
        """


__all__ = [
    "Metric",
    "MetricInputs",
    "MetricOutput",
    "MetricOutputs",
]
