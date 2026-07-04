"""Structuring anomaly metric — v2 port.

Port of legacy ``src/enrichment/tagger/anomaly_structuring.py`` (~195 LOC).
Emits three :class:`Trait` rows in the :attr:`TraitFamily.STRUCTURING`
family:

* ``anomaly.structuring.PivotFile`` (basis="cochange") — file with high
  degree in the lifetime co-change graph (legacy "many partners → pivot").
  Computed from the central ``cochange`` (file-file) relation kind so this
  shares one source of truth with the cochange relation builder; the
  Chunk-12 ``anomaly_coupling`` metric emits the **same trait name** with
  ``evidence["basis"]="coupling"`` so the two co-exist in the registry
  via canonical-id discriminator (per Chunk-12 handoff §"Open issues" #4).
* ``anomaly.structuring.IdenticalFilenames`` — file whose basename
  recurs across the project, capturing copy-paste / fork antipatterns.
* ``anomaly.structuring.TasksBottleneck`` — Jira-side bottleneck trait
  that fires on two distinct scopes:
    - **issue scope** — issue open longer than
      ``cfg.tasksbottleneck_open_age_days``.
    - **author scope** — Jira user with at least
      ``cfg.tasksbottleneck_min_in_flight`` open assigned issues.
  ``evidence["scope"]`` distinguishes them.

Reads:

* ``graph.files``         — basename grouping for IdenticalFilenames.
* ``graph.relations.of_kind("cochange")`` — pre-emitted by the file
  cochange builder (Chunk 13). When absent (e.g. tests that skip the
  builder stage) PivotFile silently emits nothing.
* ``graph.issues``        — TasksBottleneck (issue scope).
* ``graph.issue_statuses.get`` — resolve issue status category.

Why no ``Bazaar``/``Cathedral``/``Pulsar`` here despite the stub
mentioning them: those live in the legacy ``anomaly.cohesion.coordination.*``
namespace and ship via :class:`AnomalyCohesionMetric` (Chunk 15). The
stub's ``emits_traits`` was speculative; the corrected listing here
mirrors the actual legacy ``anomaly_structuring.py`` file plus
TasksBottleneck which is a structuring signal regardless of basis.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Optional

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tags import Trait, TraitFamily

if TYPE_CHECKING:
    from src.common.kernel import Graph


_TRAIT_PIVOT = "anomaly.structuring.PivotFile"
_TRAIT_IDENTICAL = "anomaly.structuring.IdenticalFilenames"
_TRAIT_TASKS_BOTTLENECK = "anomaly.structuring.TasksBottleneck"


@METRICS.register
class AnomalyStructuringMetric(Metric):
    name: ClassVar[str] = "anomaly.structuring"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=[_TRAIT_PIVOT, _TRAIT_IDENTICAL, _TRAIT_TASKS_BOTTLENECK]
    )
    config_fields: ClassVar[list[str]] = [
        "pivotfile_cochange_degree_min",
        "identical_filenames_min_count",
        "identical_filenames_peer_cap",
        "tasksbottleneck_open_age_days",
        "tasksbottleneck_min_in_flight",
        "resolved_status_categories",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        yield from _emit_pivot_cochange(graph, config)
        yield from _emit_identical_filenames(graph, config)
        yield from _emit_tasks_bottleneck(graph, config)


# ----------------------------------------------------------------------
# PivotFile (cochange basis)
# ----------------------------------------------------------------------
def _emit_pivot_cochange(graph: Any, config: Any) -> Iterable[Trait]:
    relations = getattr(graph, "relations", None)
    if relations is None or not hasattr(relations, "of_kind"):
        return
    cochange_rels = list(relations.of_kind("cochange"))
    if not cochange_rels:
        return

    threshold = int(_config_field(
        config, "pivotfile_cochange_degree_min", 10
    ))

    peers: dict[EntityRef, set[EntityRef]] = defaultdict(set)
    for rel in cochange_rels:
        src = rel.source
        tgt = rel.target
        if src == tgt:
            continue
        peers[src].add(tgt)
        peers[tgt].add(src)

    for file_ref, neighbours in peers.items():
        degree = len(neighbours)
        if degree < threshold:
            continue
        yield Trait(
            id=f"trait:{_TRAIT_PIVOT}:cochange:{file_ref.kind.value}/{file_ref.id}",
            target=file_ref,
            family=TraitFamily.STRUCTURING,
            name=_TRAIT_PIVOT,
            severity=float(degree),
            evidence={
                "basis": "cochange",
                "cochange_degree": int(degree),
                "threshold": int(threshold),
            },
        )


# ----------------------------------------------------------------------
# IdenticalFilenames
# ----------------------------------------------------------------------
def _emit_identical_filenames(graph: Any, config: Any) -> Iterable[Trait]:
    files = _safe_iter(getattr(graph, "files", None))
    if not files:
        return

    min_count = int(_config_field(config, "identical_filenames_min_count", 2))
    peer_cap = int(_config_field(config, "identical_filenames_peer_cap", 20))

    groups: dict[str, list[Any]] = defaultdict(list)
    for file_ in files:
        path = getattr(file_, "path", None) or file_.id
        if not path:
            continue
        idx = path.rfind("/")
        base = path if idx == -1 else path[idx + 1:]
        groups[base].append(file_)

    for base, members in groups.items():
        # min_count of 2 means "at least 2 files" — match the legacy
        # check "len(members) >= min_count" (peer count = members - 1).
        if len(members) < max(min_count, 2):
            continue
        for file_ in members:
            file_ref = file_.ref()
            peers = [m.ref() for m in members if m is not file_]
            peer_count = len(peers)
            trimmed = [p.id for p in peers[:peer_cap]]
            yield Trait(
                id=f"trait:{_TRAIT_IDENTICAL}:{file_ref.kind.value}/{file_ref.id}",
                target=file_ref,
                family=TraitFamily.STRUCTURING,
                name=_TRAIT_IDENTICAL,
                severity=float(peer_count),
                evidence={
                    "basename": base,
                    "peer_count": peer_count,
                    "peer_file_ids": trimmed,
                    "peer_cap": peer_cap,
                    "threshold": int(min_count),
                },
            )


# ----------------------------------------------------------------------
# TasksBottleneck (issue + author scopes)
# ----------------------------------------------------------------------
def _emit_tasks_bottleneck(graph: Any, config: Any) -> Iterable[Trait]:
    issues = _safe_iter(getattr(graph, "issues", None))
    if not issues:
        return

    age_min = int(_config_field(config, "tasksbottleneck_open_age_days", 180))
    min_in_flight = int(_config_field(config, "tasksbottleneck_min_in_flight", 10))
    resolved_categories = _config_field(
        config, "resolved_status_categories",
        ("done", "closed", "resolved", "complete", "completed"),
    )
    resolved_set = {c.lower() for c in resolved_categories}

    statuses_reg = getattr(graph, "issue_statuses", None)
    anchor = _anchor_now(graph)

    in_flight_by_assignee: dict[EntityRef, int] = defaultdict(int)

    for issue in issues:
        if not _is_issue_open(issue, statuses_reg, resolved_set):
            continue
        created = ensure_aware(getattr(issue, "created_at", None))
        if created is not None:
            age_days = (anchor - created).days
            if age_days >= age_min:
                issue_ref = issue.ref()
                yield Trait(
                    id=f"trait:{_TRAIT_TASKS_BOTTLENECK}:issue:{issue_ref.kind.value}/{issue_ref.id}",
                    target=issue_ref,
                    family=TraitFamily.STRUCTURING,
                    name=_TRAIT_TASKS_BOTTLENECK,
                    severity=float(age_days),
                    evidence={
                        "open_age_days": int(age_days),
                        "threshold": int(age_min),
                        "scope": "issue",
                    },
                )

        for assignee_ref in getattr(issue, "assignee_refs", None) or []:
            in_flight_by_assignee[assignee_ref] += 1

    for assignee_ref, count in in_flight_by_assignee.items():
        if count < min_in_flight:
            continue
        yield Trait(
            id=f"trait:{_TRAIT_TASKS_BOTTLENECK}:author:{assignee_ref.kind.value}/{assignee_ref.id}",
            target=assignee_ref,
            family=TraitFamily.STRUCTURING,
            name=_TRAIT_TASKS_BOTTLENECK,
            severity=float(count),
            evidence={
                "in_flight_issues": int(count),
                "threshold": int(min_in_flight),
                "scope": "author",
            },
        )


# ----------------------------------------------------------------------
# Shared helpers
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


def _anchor_now(graph: Any) -> datetime:
    """Anchor for age computations.

    Honours an explicit ``graph.anchor_date`` when present (legacy
    test-stub convention) and otherwise falls back to wall-clock now.
    """
    explicit = getattr(graph, "anchor_date", None)
    if explicit is not None:
        d = ensure_aware(explicit)
        if d is not None:
            return d
    return datetime.now(timezone.utc)


def _is_issue_open(issue: Any, statuses_reg: Any, resolved: set[str]) -> bool:
    """``True`` when the issue's current status category is not in
    ``resolved``. Falls back to "open" when status is unresolvable."""
    if getattr(issue, "resolution_date", None) is not None:
        return False
    status_ref = getattr(issue, "status_ref", None)
    if status_ref is None or statuses_reg is None:
        return True
    status = statuses_reg.get(status_ref.id) if hasattr(statuses_reg, "get") else None
    if status is None:
        return True
    cat = (getattr(status, "category", None) or "").strip().lower()
    if cat in resolved:
        return False
    name = (getattr(status, "name", None) or "").strip().lower()
    return name not in resolved


__all__ = ["AnomalyStructuringMetric"]
