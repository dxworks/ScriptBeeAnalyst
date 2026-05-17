"""Complexity anomaly metric — v2 port.

Port of legacy ``src/enrichment/tagger/anomaly_complexity.py`` (~94 LOC).
Emits ``anomaly.cohesion.size.DynamicBlob`` :class:`Trait` rows on files
that exhibit BOTH high lifetime LOC AND a high lifetime change count.

dx port (DynamicBlob.java lines 27–56):

* Fires only when the Lizard-derived ``sum_nloc`` is
  ``>= cfg.dynamicblob_loc_min`` AND the file's change count is
  ``>= cfg.dynamicblob_changes_min``.
* Severity = ``1 + LOC bucket bonus (up to 5) + churn bucket bonus
  (up to 4)``, clamped to ``10`` to match dx's AnomaliesRegistry
  ``normalizedValue`` range.

The trait :pyattr:`family` is :attr:`TraitFamily.COHESION` — the legacy
docstring placed this signal in the cohesion family ("size cohesion")
even though the file name is ``anomaly_complexity.py``. The Chunk-11
``TraitFamily`` enum has both ``COHESION`` and ``SMELL``; sticking to
``COHESION`` keeps the legacy trait-name namespace
(``anomaly.cohesion.size.*``) intact.

Reads from the host: ``files`` (whole registry), ``file_metrics``
(``by_file`` + ``by_name`` indexes), ``changes`` (``by_file`` index).
Skips files for which Lizard has no ``sum_nloc`` row.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Optional

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Trait, TraitFamily

if TYPE_CHECKING:
    from src.common.kernel import Graph


_DEFAULT_LOC_MIN = 500
_DEFAULT_CHANGES_MIN = 20

_TRAIT_NAME = "anomaly.cohesion.size.DynamicBlob"


@METRICS.register
class AnomalyComplexityMetric(Metric):
    name: ClassVar[str] = "anomaly.complexity"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=[_TRAIT_NAME]
    )
    config_fields: ClassVar[list[str]] = [
        "dynamicblob_loc_min",
        "dynamicblob_changes_min",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        files = _safe_iter(getattr(graph, "files", None))
        if not files:
            return

        loc_min = int(_config_field(config, "dynamicblob_loc_min", _DEFAULT_LOC_MIN))
        changes_min = int(_config_field(config, "dynamicblob_changes_min", _DEFAULT_CHANGES_MIN))

        sum_nloc_by_file = _sum_nloc_index(graph)
        max_ccn_by_file = _file_metric_index(graph, "max_ccn")
        avg_ccn_by_file = _file_metric_index(graph, "avg_ccn")
        changes_by_file = _changes_by_file_index(graph)

        for file_ in files:
            file_ref = file_.ref()
            loc = sum_nloc_by_file.get(file_ref)
            if loc is None:
                continue
            change_count = len(list(changes_by_file(file_ref)))
            if loc < loc_min or change_count < changes_min:
                continue
            severity = _dynamicblob_severity(loc, change_count, loc_min, changes_min)
            evidence: dict[str, Any] = {
                "loc": int(loc),
                "changes": int(change_count),
                "threshold_loc": loc_min,
                "threshold_changes": changes_min,
            }
            max_ccn = max_ccn_by_file.get(file_ref)
            if max_ccn is not None:
                evidence["max_ccn"] = float(max_ccn)
            avg_ccn = avg_ccn_by_file.get(file_ref)
            if avg_ccn is not None:
                evidence["avg_ccn"] = float(avg_ccn)
            yield Trait(
                id=f"trait:{_TRAIT_NAME}:{file_ref.kind.value}/{file_ref.id}",
                target=file_ref,
                family=TraitFamily.COHESION,
                name=_TRAIT_NAME,
                severity=float(severity),
                evidence=evidence,
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


def _file_metric_index(graph: Any, metric_name: str) -> dict[Any, float]:
    """Return ``{file_ref: value}`` for a given Lizard metric name.

    Single-pass scan over ``graph.file_metrics`` filtered by
    ``metric_name``. Materialised once per pipeline pass (callers iterate
    files; the inner per-file lookup is O(1)).
    """
    file_metrics = getattr(graph, "file_metrics", None)
    if file_metrics is None:
        return {}
    out: dict[Any, float] = {}
    by_name = getattr(file_metrics, "by_name", None)
    if by_name is not None:
        rows = by_name[metric_name]
    else:
        try:
            rows = [m for m in file_metrics if getattr(m, "metric_name", None) == metric_name]
        except TypeError:
            return {}
    for row in rows:
        fref = getattr(row, "file_ref", None)
        val = getattr(row, "value", None)
        if fref is None or val is None:
            continue
        out[fref] = float(val)
    return out


def _sum_nloc_index(graph: Any) -> dict[Any, float]:
    return _file_metric_index(graph, "sum_nloc")


def _changes_by_file_index(graph: Any):
    """Return a callable ``file_ref -> Iterable[Change]``."""
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _ref: ()
    by_file = getattr(changes, "by_file", None)
    if by_file is not None:
        return lambda file_ref: by_file[file_ref]

    def scan(file_ref):
        return [ch for ch in changes if getattr(ch, "file_ref", None) == file_ref]

    return scan


def _dynamicblob_severity(
    loc: float, changes: int, loc_min: int, changes_min: int
) -> int:
    """dx port: 1 + LOC bonus (up to 5) + churn bonus (up to 4), clamped at 10."""
    severity = 1
    if loc >= loc_min * 5:
        severity += 5
    elif loc >= loc_min * 3:
        severity += 3
    elif loc >= loc_min * 2:
        severity += 1

    if changes >= changes_min * 3:
        severity += 4
    elif changes >= changes_min * 1.5:
        severity += 2

    return min(severity, 10)


__all__ = ["AnomalyComplexityMetric"]
