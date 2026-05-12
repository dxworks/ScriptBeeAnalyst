"""Complexity anomaly metric — DEFERRED stub.

Port of legacy ``src/enrichment/tagger/anomaly_complexity.py`` (~94 LOC).
Emits ``anomaly.smell.DynamicBlob`` traits (high-LOC + high-churn files).
Depends on the v2 Lizard ``FileMetric`` registry. See handoff §"Deferred
ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Trait

if TYPE_CHECKING:
    from src.common.kernel import Graph


@METRICS.register
class AnomalyComplexityMetric(Metric):
    name: ClassVar[str] = "anomaly.complexity"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=["anomaly.smell.DynamicBlob"]
    )
    config_fields: ClassVar[list[str]] = [
        "dynamicblob_loc_min",
        "dynamicblob_changes_min",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        raise NotImplementedError(
            "AnomalyComplexityMetric port deferred — depends on Lizard "
            "FileMetric registry wiring. See Chunk 7 handoff."
        )


__all__ = ["AnomalyComplexityMetric"]
