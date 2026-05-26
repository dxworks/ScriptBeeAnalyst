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

Phase split (UnifiedUsers redesign §H — task P4.B)
--------------------------------------------------

The pipeline is split into two phases keyed to the project lifecycle:

* :func:`run_pipeline_phase_a` runs at ``/projects/{id}/build``, BEFORE
  the rebind pass. It covers every builder / metric whose source and
  target avoid Account refs — the "non-people" set. Roughly the whole
  catalog minus the people-side builders below.
* :func:`run_pipeline_phase_b` runs at ``/projects/{id}/finalize``,
  AFTER :func:`src.smart_merge.rebind.rebind_account_refs_to_unified`
  has rewritten every role-typed account ref to target a
  :class:`UnifiedUser`. It covers the people-side classifiers, traits,
  and relation builders: their inputs read author / committer /
  reviewer / reporter / assignee refs (now ``UNIFIED_USER``-kinded),
  and their outputs are keyed on those refs.

The partition is maintained as three hardcoded sets at the top of this
module (:data:`_PHASE_B_RELATION_KINDS`, :data:`_PHASE_B_METRIC_NAMES`,
:data:`_PHASE_B_OVERVIEW_NAMES`) — no class-level metadata introspection.
Adding a new people-side computation means appending its name to the
relevant set.

The legacy :func:`run_pipeline` entry point stays as a backwards-compatible
alias that runs ``phase_a`` then ``phase_b`` in sequence. Callers that
do NOT yet split build vs. finalize get today's "all in one pass"
behaviour. New callers (the upcoming P4.A finalize endpoint) call
``phase_a`` at build time and ``phase_b`` at finalize time so phase B
runs against a graph whose role refs have already been rebound.

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
from src.enrichment.relations import (
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
# caller that does ``from src.enrichment.pipeline import run_pipeline``
# and then runs it against the default catalogs sees empty registries
# (review round 2 blocking #1 fix).
#
# Why import here and not in ``src.enrichment.metrics.__init__`` /
# ``src.enrichment.relations.__init__``? Chunk 3 deliberately kept
# those packages free of implementation deps so the bare ABCs stayed
# importable in isolation (e.g. for chunk-3-only tests that build a
# private ``MetricRegistry`` without firing the chunk-7 implementations).
# The pipeline module is the right place because anyone importing
# ``run_pipeline`` definitionally wants the implementations active.
import src.enrichment.relations.implementations  # noqa: F401, E402
import src.enrichment.metrics.implementations  # noqa: F401, E402


# ----------------------------------------------------------------------
# Phase A / Phase B partition (UnifiedUsers redesign §H)
# ----------------------------------------------------------------------
# Plain Python sets — no class-level introspection. Adding a new
# people-side computation means appending its registered name here.
#
# Phase B inclusion criteria (any one suffices):
#   1. The builder/metric reads or writes a role-typed account ref
#      (``author_ref``, ``committer_ref``, ``reviewer_ref``,
#      ``reporter_ref``, ``creator_ref``, ``assignee_ref``,
#      ``merged_by_ref``) directly.
#   2. The builder/metric is gated by one of the four flipped
#      ``target.kind == UNIFIED_USER`` checks (today: ``anomaly.knowledge``
#      via the activity-classifier lookup in ``_build_activity_lookup``
#      / ``_active_author_total`` and via
#      ``file_trait_utils.active_author_churn``).
#   3. The builder consumes phase-B output relations (transitive
#      dependency: e.g. ``cochange.file_shared_devs`` reads ``ownership``
#      edges; those carry the author ref so they MUST be emitted
#      post-rebind to land in the right kind bucket).
#
# Everything not in these sets is Phase A.

#: Relation builder ``.name`` values that read or emit author-side refs.
#: Iterated in :func:`run_pipeline_phase_b`.
_PHASE_B_RELATION_KINDS: frozenset[str] = frozenset({
    # Direct author-side emitters — source or target is an author ref.
    "coauthor",                              # author ↔ author
    "ownership",                             # author → file
    "pr.reviewer",                           # PR → reviewer
    "cochange.author_time_windowed",         # author ↔ author
    "cochange.author_shared_task_prefixes",  # author ↔ author
    # Transitive — consume ``ownership`` / author-keyed cochange edges.
    # Must run post-rebind so the per-file author set carries UU refs.
    "cochange.file_shared_devs",             # reads ownership relations
    "cochange.component_shared_devs",        # rolls up file_shared_devs
})

#: Metric ``.name`` values that touch author-side targets.
#: Iterated in :func:`run_pipeline_phase_b`.
_PHASE_B_METRIC_NAMES: frozenset[str] = frozenset({
    # Emits ``activity`` / ``seniority`` classifiers keyed on the author
    # principal (GIT_ACCOUNT pre-finalize, UNIFIED_USER post-finalize —
    # see ``_resolve_author_principals`` in author_classifiers.py).
    "author.classifiers",
    # Gated by the activity-classifier UU kind-check (see
    # ``_build_activity_lookup`` / ``_active_author_total`` flips). Also
    # emits the OrphanCausers trait keyed on the account ref.
    "anomaly.knowledge",
    # Author distribution / inter-commit-spacing signals — counts
    # *distinct authors per file*; that count is people-identity-keyed,
    # so it must run post-rebind to give a "per unified person"
    # measurement rather than "per per-source signature".
    "anomaly.cohesion",
    # Emits ``TasksBottleneck`` keyed on Jira ``assignee_ref`` (which
    # is a role-typed account ref).
    "anomaly.structuring",
})

#: Overview-table ``.name`` values whose row targets are author-side.
#: NOT iterated by :func:`run_pipeline_phase_b` directly — overviews are
#: queried lazily through the MCP sandbox (``OVERVIEWS.get(name).build``).
#: This set documents which overviews must be rebuilt at finalize time
#: when the upcoming P4.A finalize endpoint regenerates the cached UI
#: payloads. Kept here as a single source of truth alongside the metric
#: + relation sets.
_PHASE_B_OVERVIEW_NAMES: frozenset[str] = frozenset({
    "authorship",
    "knowledge",
})


def phase_b_relation_kinds() -> frozenset[str]:
    """Snapshot of the people-side relation builder names."""
    return _PHASE_B_RELATION_KINDS


def phase_b_metric_names() -> frozenset[str]:
    """Snapshot of the people-side metric names."""
    return _PHASE_B_METRIC_NAMES


def phase_b_overview_names() -> frozenset[str]:
    """Snapshot of the people-side overview-table names."""
    return _PHASE_B_OVERVIEW_NAMES


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
# Core driver — shared between phases and the back-compat alias.
# ----------------------------------------------------------------------
def _run(
    host: Any,
    config: Any,
    *,
    builder_classes: List[type[RelationBuilder]],
    metric_classes: List[type[Metric]],
    result: PipelineResult,
) -> PipelineResult:
    """Execute the supplied catalogs against ``host``, writing results.

    Shared by :func:`run_pipeline_phase_a`, :func:`run_pipeline_phase_b`,
    and the back-compat :func:`run_pipeline` alias.
    """
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


def _resolve_catalogs(
    builders: Optional[Iterable[type[RelationBuilder]]],
    metrics: Optional[Iterable[type[Metric]]],
) -> tuple[List[type[RelationBuilder]], List[type[Metric]]]:
    """Materialise the registered catalogs (or honor the override)."""
    builder_classes: List[type[RelationBuilder]] = (
        list(BUILDERS) if builders is None else list(builders)
    )
    metric_classes: List[type[Metric]] = (
        list(METRICS) if metrics is None else list(metrics)
    )
    return builder_classes, metric_classes


def _split_phase(
    builder_classes: List[type[RelationBuilder]],
    metric_classes: List[type[Metric]],
    *,
    phase: str,
) -> tuple[List[type[RelationBuilder]], List[type[Metric]]]:
    """Filter the supplied catalogs to the requested phase.

    ``phase == "a"`` keeps every step NOT in the people-side sets.
    ``phase == "b"`` keeps only the people-side steps.
    """
    if phase == "a":
        return (
            [
                b for b in builder_classes
                if getattr(b, "name", b.__name__) not in _PHASE_B_RELATION_KINDS
            ],
            [
                m for m in metric_classes
                if getattr(m, "name", m.__name__) not in _PHASE_B_METRIC_NAMES
            ],
        )
    if phase == "b":
        return (
            [
                b for b in builder_classes
                if getattr(b, "name", b.__name__) in _PHASE_B_RELATION_KINDS
            ],
            [
                m for m in metric_classes
                if getattr(m, "name", m.__name__) in _PHASE_B_METRIC_NAMES
            ],
        )
    raise ValueError(f"unknown phase: {phase!r} (want 'a' or 'b')")


# ----------------------------------------------------------------------
# Public phase runners
# ----------------------------------------------------------------------
def run_pipeline_phase_a(
    host: Any,
    config: Any,
    *,
    builders: Optional[Iterable[type[RelationBuilder]]] = None,
    metrics: Optional[Iterable[type[Metric]]] = None,
) -> PipelineResult:
    """Run the non-people half of the catalog (UnifiedUsers redesign §H).

    Intended to run at ``/projects/{id}/build``, BEFORE the rebind pass.
    Every builder / metric whose ``name`` is NOT in
    :data:`_PHASE_B_RELATION_KINDS` / :data:`_PHASE_B_METRIC_NAMES`
    executes here.

    Catalogs may be overridden for test isolation (same semantics as
    :func:`run_pipeline`).
    """
    builder_classes, metric_classes = _resolve_catalogs(builders, metrics)
    builders_a, metrics_a = _split_phase(
        builder_classes, metric_classes, phase="a"
    )
    return _run(
        host,
        config,
        builder_classes=builders_a,
        metric_classes=metrics_a,
        result=PipelineResult(),
    )


def run_pipeline_phase_b(
    host: Any,
    config: Any,
    *,
    builders: Optional[Iterable[type[RelationBuilder]]] = None,
    metrics: Optional[Iterable[type[Metric]]] = None,
) -> PipelineResult:
    """Run the people-side half of the catalog (UnifiedUsers redesign §H).

    Intended to run at ``/projects/{id}/finalize``, AFTER
    :func:`src.smart_merge.rebind.rebind_account_refs_to_unified` has
    rewritten every role-typed account ref to target a UnifiedUser.

    Every builder / metric whose ``name`` is in
    :data:`_PHASE_B_RELATION_KINDS` / :data:`_PHASE_B_METRIC_NAMES`
    executes here.

    Catalogs may be overridden for test isolation (same semantics as
    :func:`run_pipeline`).
    """
    builder_classes, metric_classes = _resolve_catalogs(builders, metrics)
    builders_b, metrics_b = _split_phase(
        builder_classes, metric_classes, phase="b"
    )
    return _run(
        host,
        config,
        builder_classes=builders_b,
        metric_classes=metrics_b,
        result=PipelineResult(),
    )


# ----------------------------------------------------------------------
# Back-compat single-shot alias
# ----------------------------------------------------------------------
def run_pipeline(
    host: Any,
    config: Any,
    *,
    builders: Optional[Iterable[type[RelationBuilder]]] = None,
    metrics: Optional[Iterable[type[Metric]]] = None,
) -> PipelineResult:
    """Run every registered builder + metric against ``host`` in one pass.

    .. deprecated::
       Prefer :func:`run_pipeline_phase_a` (at build time) +
       :func:`run_pipeline_phase_b` (at finalize, after the rebind
       pass). This single-shot alias keeps callers that have not been
       migrated to the new lifecycle working — the phase-A + phase-B
       chain produces the same total output set as the alias.

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
        Identical totals to ``phase_a`` + ``phase_b`` chained on the
        same host.
    """
    # Resolve the catalogs ONCE so the phase split sees a stable
    # snapshot — callers may pass an iterable that drains after one pass.
    builder_classes, metric_classes = _resolve_catalogs(builders, metrics)

    result = PipelineResult()

    # Phase A — non-people computations.
    builders_a, metrics_a = _split_phase(
        builder_classes, metric_classes, phase="a"
    )
    _run(
        host,
        config,
        builder_classes=builders_a,
        metric_classes=metrics_a,
        result=result,
    )

    # Phase B — people-side computations. The alias does NOT run the
    # rebind pass between phases — by design, callers using this entry
    # point are pre-lifecycle code (no finalize step). The order of
    # emission inside ``result`` matches the order of the catalogs as
    # registered.
    builders_b, metrics_b = _split_phase(
        builder_classes, metric_classes, phase="b"
    )
    _run(
        host,
        config,
        builder_classes=builders_b,
        metric_classes=metrics_b,
        result=result,
    )

    return result


__all__ = [
    "PipelineError",
    "PipelineHost",
    "PipelineResult",
    "phase_b_metric_names",
    "phase_b_overview_names",
    "phase_b_relation_kinds",
    "run_pipeline",
    "run_pipeline_phase_a",
    "run_pipeline_phase_b",
]
