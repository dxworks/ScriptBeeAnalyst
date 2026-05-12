"""Coupling anomaly metric — DEFERRED stub.

Port of legacy ``src/enrichment/tagger/anomaly_coupling.py`` (~69 LOC).
Emits ``anomaly.coupling.PivotFile`` traits (files with high co-change
degree). Depends on the v2 ``cochange`` relation being populated AND a
per-file degree count on top of ``RelationRegistry.by_source``. See
handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Trait

if TYPE_CHECKING:
    from src.common.kernel import Graph


@METRICS.register
class AnomalyCouplingMetric(Metric):
    name: ClassVar[str] = "anomaly.coupling"
    inputs: ClassVar[MetricInputs] = MetricInputs(
        source_kind=EntityKind.FILE,
        relation_kind="cochange",
    )
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=["anomaly.coupling.PivotFile"]
    )
    config_fields: ClassVar[list[str]] = ["pivotfile_cochange_degree_min"]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        raise NotImplementedError(
            "AnomalyCouplingMetric port deferred — needs cochange relations "
            "populated + degree computation. See Chunk 7 handoff."
        )


__all__ = ["AnomalyCouplingMetric"]
