"""Structuring anomaly metric — DEFERRED stub.

Port of legacy ``src/enrichment/tagger/anomaly_structuring.py``
(~195 LOC). Emits Bazaar, Cathedral, Pulsar, PivotFile, IdenticalFilenames
traits in the ``STRUCTURING`` family. Depends on cochange relations +
file_trait_utils. See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Trait

if TYPE_CHECKING:
    from src.common.kernel import Graph


@METRICS.register
class AnomalyStructuringMetric(Metric):
    name: ClassVar[str] = "anomaly.structuring"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=[
            "anomaly.structuring.Bazaar",
            "anomaly.structuring.Cathedral",
            "anomaly.structuring.Pulsar",
            "anomaly.structuring.PivotFile",
            "anomaly.structuring.IdenticalFilenames",
        ]
    )
    config_fields: ClassVar[list[str]] = [
        "bazaar_distinct_authors_min",
        "cathedral_dominance_ratio",
        "cathedral_min_recent_commits",
        "pulsar_cv_min",
        "pulsar_min_commits",
        "pulsar_min_intervals",
        "pivotfile_cochange_degree_min",
        "identical_filenames_min_count",
        "identical_filenames_peer_cap",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        raise NotImplementedError(
            "AnomalyStructuringMetric port deferred — depends on file_trait_utils "
            "v2 port AND cochange relations. See Chunk 7 handoff."
        )


__all__ = ["AnomalyStructuringMetric"]
