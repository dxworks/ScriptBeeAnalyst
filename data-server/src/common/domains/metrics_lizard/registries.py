"""Registries for every Lizard-metrics-domain :class:`Entity` subclass.

Per plan §1.5, indexes are declared as a ``ClassVar[list[IndexSpec]]`` and
rebuilt on every mutation / on :meth:`Registry.load` — they are NOT pickled.
"""
from __future__ import annotations

from ...kernel import IndexSpec, Registry
from .models import FileMetric, LizardMetricsProject


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class LizardMetricsProjectRegistry(Registry[LizardMetricsProject, str]):
    """Holds every :class:`LizardMetricsProject` in the graph."""

    indexes = [
        IndexSpec(name="by_name", key_fn=lambda p: p.name, multi=True),
    ]

    def get_id(self, entity: LizardMetricsProject) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# File metrics
# ---------------------------------------------------------------------------


class FileMetricRegistry(Registry[FileMetric, str]):
    """Every :class:`FileMetric` in the graph.

    Indexes (plan §4.1 + handoff):

    * ``by_file``    — fast "all metrics for file F" lookup. Pair this
                       with ``by_name`` for the file-level dashboard's
                       "show me CCN for this file" path.
    * ``by_project`` — one bucket per :class:`LizardMetricsProject`.
    * ``by_name``    — "all CCN values across the project" — the metric
                       layer uses this for percentile / outlier
                       detection.

    Skipped: ``by_name_value_range``. The brief explicitly allows
    skipping the range index. Range queries are O(N) across one
    metric_name's bucket (small per-metric since N here is at most the
    file count) — not worth a custom index structure.
    """

    indexes = [
        IndexSpec(name="by_file", key_fn=lambda m: m.file_ref, multi=True),
        IndexSpec(name="by_project", key_fn=lambda m: m.project_ref, multi=True),
        IndexSpec(name="by_name", key_fn=lambda m: m.metric_name, multi=True),
    ]

    def get_id(self, entity: FileMetric) -> str:
        return entity.id


__all__ = [
    "FileMetricRegistry",
    "LizardMetricsProjectRegistry",
]
