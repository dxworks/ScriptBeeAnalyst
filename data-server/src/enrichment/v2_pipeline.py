"""Enrichment pipeline driver (v2).

See plan §12 step 4 and the Chunk-7 brief. The pipeline:

1. Runs every registered :class:`RelationBuilder` and writes its outputs
   into ``host.relations``.
2. Runs every registered :class:`Metric` and routes each yielded item
   into the correct registry by ``isinstance`` check:

   * :class:`Trait`      → ``host.traits``
   * :class:`Classifier` → ``host.classifiers``
   * :class:`Relation`   → ``host.relations``

3. Catches per-step exceptions; records them in :attr:`PipelineResult.errors`;
   continues — a failing metric does NOT abort the whole pipeline.

4. Returns a :class:`PipelineResult` summary.

Why a ``PipelineHost`` protocol (not a real :class:`Graph`)?
-----------------------------------------------------------

Chunk 8 swaps the kernel's dict-of-registries for typed registry fields
on :class:`Graph`. Until that lands, ``run_pipeline`` accepts any object
that exposes the three target registries; this keeps Chunk 7 testable
against a tiny stub host without requiring the full Graph wiring.
Chunk 8 will pass a real :class:`Graph` (which by then will have
typed ``traits`` / ``classifiers`` / ``relations`` fields satisfying the
protocol automatically).

Mutation contract
-----------------

Builders and metrics are pure (per their ABC contracts). The pipeline is
the **only** code that writes into the three target registries. This
keeps the "metric is a pure function of (graph, config)" invariant testable.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Protocol

from pydantic import BaseModel, ConfigDict

from src.enrichment.metrics import METRICS, Metric
from src.enrichment.relations_v2 import (
    BUILDERS,
    Relation,
    RelationBuilder,
    RelationRegistry,
)
from src.enrichment.tags import (
    Classifier,
    ClassifierRegistry,
    Trait,
    TraitRegistry,
)

# Side-effect imports — pull every shipped implementation module into the
# process so the ``@BUILDERS.register`` / ``@METRICS.register`` decorators
# fire and populate the module-level singletons. Without this, a Chunk-8
# caller that does ``from src.enrichment.v2_pipeline import run_pipeline``
# and then runs it against the default catalogs sees empty registries
# (review round 2 blocking #1 fix).
#
# Why import here and not in ``src.enrichment.metrics.__init__`` /
# ``src.enrichment.relations_v2.__init__``? Chunk 3 deliberately kept
# those packages free of implementation deps so the bare ABCs stayed
# importable in isolation (e.g. for chunk-3-only tests that build a
# private ``MetricRegistry`` without firing the chunk-7 implementations).
# The pipeline module is the right place because anyone importing
# ``run_pipeline`` definitionally wants the implementations active.
import src.enrichment.relations_v2.implementations  # noqa: F401, E402
import src.enrichment.metrics.implementations  # noqa: F401, E402


# ----------------------------------------------------------------------
# PipelineHost protocol
# ----------------------------------------------------------------------
class PipelineHost(Protocol):
    """The minimal surface :func:`run_pipeline` requires from its host.

    A real :class:`Graph` (Chunk 8) satisfies this automatically once
    ``traits``/``classifiers``/``relations`` are typed registry fields.
    Chunk-7 tests use a lightweight stub that only carries the three
    target registries (plus whatever read-only attrs the metrics being
    tested touch).
    """

    relations: RelationRegistry
    traits: TraitRegistry
    classifiers: ClassifierRegistry


# ----------------------------------------------------------------------
# Result model
# ----------------------------------------------------------------------
class PipelineError(BaseModel):
    """A single per-step failure recorded by :func:`run_pipeline`.

    The pipeline does not abort on a single failure — it records the
    error and continues to the next builder/metric. The MCP / web UI
    can surface these so the user sees "metric X failed but graph is
    still usable".
    """

    model_config = ConfigDict(extra="forbid")

    step: str
    """Either ``"builder"`` or ``"metric"``."""

    name: str
    """The ``RelationBuilder.name`` or ``Metric.name`` that failed."""

    error_type: str
    """The exception class name (``type(exc).__name__``)."""

    message: str
    """The exception's ``str(exc)``."""


class PipelineResult(BaseModel):
    """Summary of one :func:`run_pipeline` invocation.

    Fields mirror the Chunk-7 brief:

    * ``metrics_run`` / ``builders_run`` — names of every step that
      executed successfully (an entry here does NOT preclude an error
      on a different step — both are recorded independently).
    * ``traits_emitted`` / ``classifiers_emitted`` / ``relations_emitted``
      — totals across the whole run.
    * ``errors`` — per-step failures (see :class:`PipelineError`).
    """

    model_config = ConfigDict(extra="forbid")

    metrics_run: List[str] = []
    builders_run: List[str] = []
    traits_emitted: int = 0
    classifiers_emitted: int = 0
    relations_emitted: int = 0
    errors: List[PipelineError] = []


# ----------------------------------------------------------------------
# Helpers — registry write-through
# ----------------------------------------------------------------------
def _add_relation(host: PipelineHost, rel: Relation) -> None:
    host.relations.add(rel)


def _add_trait(host: PipelineHost, trait: Trait) -> None:
    host.traits.add(trait)


def _add_classifier(host: PipelineHost, classifier: Classifier) -> None:
    host.classifiers.add(classifier)


# ----------------------------------------------------------------------
# The driver
# ----------------------------------------------------------------------
def run_pipeline(
    host: Any,
    config: Any,
    *,
    builders: Optional[Iterable[type[RelationBuilder]]] = None,
    metrics: Optional[Iterable[type[Metric]]] = None,
) -> PipelineResult:
    """Run every registered builder + metric against ``host``.

    Parameters
    ----------
    host
        A :class:`PipelineHost` — either a real :class:`Graph` (Chunk 8)
        or a Chunk-7 test stub. Must expose ``relations``, ``traits``,
        and ``classifiers`` registry-like objects with an ``add(entity)``
        method.

    config
        The legacy :class:`EnrichmentConfig` (or any successor in Chunks
        7+). Passed through to ``Metric.compute(graph, config)`` and
        ``RelationBuilder.build(graph)`` (builders may inspect ``config``
        too — see :meth:`RelationBuilder.build` — but most don't).

    builders
        Optional override of the builder catalog. Defaults to every
        builder registered with :data:`BUILDERS`. Pass an empty iterable
        to skip the builder stage entirely.

    metrics
        Optional override of the metric catalog. Defaults to every
        metric registered with :data:`METRICS`. Pass an empty iterable
        to skip the metric stage entirely.

    Returns
    -------
    PipelineResult
        Per-step success names + emission counts + per-step errors.
    """
    # Resolve the catalogs once so callers can pass a snapshot of either.
    builder_classes: List[type[RelationBuilder]] = (
        list(BUILDERS) if builders is None else list(builders)
    )
    metric_classes: List[type[Metric]] = (
        list(METRICS) if metrics is None else list(metrics)
    )

    result = PipelineResult()

    # ------------------------------------------------------------------
    # Stage 1 — relation builders
    # ------------------------------------------------------------------
    for builder_cls in builder_classes:
        builder_name = getattr(builder_cls, "name", builder_cls.__name__)
        try:
            builder = builder_cls()
            for rel in builder.build(host):
                _add_relation(host, rel)
                result.relations_emitted += 1
            result.builders_run.append(builder_name)
        except Exception as exc:  # noqa: BLE001 — pipeline policy: catch + record
            result.errors.append(
                PipelineError(
                    step="builder",
                    name=builder_name,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            )

    # ------------------------------------------------------------------
    # Stage 2 — metrics
    # ------------------------------------------------------------------
    for metric_cls in metric_classes:
        metric_name = getattr(metric_cls, "name", metric_cls.__name__)
        try:
            metric = metric_cls()
            for emitted in metric.compute(host, config):
                # ``Classifier`` and ``Trait`` are sibling concrete leaves
                # under ``Tag`` (per chunk-3 §5.1); ``Relation`` is a
                # disjoint leaf under ``Entity``. isinstance order between
                # ``Classifier`` and ``Trait`` is therefore irrelevant for
                # disambiguation — neither one is an ancestor of the
                # other. The three checks are listed in the order the
                # docstring above documents (Trait / Classifier /
                # Relation).
                if isinstance(emitted, Trait):
                    _add_trait(host, emitted)
                    result.traits_emitted += 1
                elif isinstance(emitted, Classifier):
                    _add_classifier(host, emitted)
                    result.classifiers_emitted += 1
                elif isinstance(emitted, Relation):
                    _add_relation(host, emitted)
                    result.relations_emitted += 1
                else:
                    # Anything else is a metric authoring bug — capture
                    # it as an error per metric, but don't abort the
                    # pipeline.
                    raise TypeError(
                        f"Metric {metric_name!r} yielded an unsupported "
                        f"object of type {type(emitted).__name__}"
                    )
            result.metrics_run.append(metric_name)
        except Exception as exc:  # noqa: BLE001 — pipeline policy: catch + record
            result.errors.append(
                PipelineError(
                    step="metric",
                    name=metric_name,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            )

    return result


__all__ = [
    "PipelineError",
    "PipelineHost",
    "PipelineResult",
    "run_pipeline",
]
