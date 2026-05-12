"""Quality-issues anomaly metric — DEFERRED stub.

Port of legacy ``src/enrichment/tagger/anomaly_quality_issues.py``
(~117 LOC). Re-emits each :class:`QualityIssue` row as an
``anomaly.smell.<RuleId>`` trait on the affected file.

Maps the legacy ``family="codesmell"`` string to
``TraitFamily.SMELL`` (renamed in chunk-3). Depends on
:class:`QualityIssueRegistry` being wired (Chunk 6 ships the model;
Chunk 8 wires it on Graph).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Trait

if TYPE_CHECKING:
    from src.common.kernel import Graph


@METRICS.register
class AnomalyQualityIssuesMetric(Metric):
    name: ClassVar[str] = "anomaly.quality_issues"
    inputs: ClassVar[MetricInputs] = MetricInputs(
        source_kind=EntityKind.QUALITY_ISSUE
    )
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=["anomaly.smell.*"]
    )
    config_fields: ClassVar[list[str]] = []

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        raise NotImplementedError(
            "AnomalyQualityIssuesMetric port deferred — needs QualityIssueRegistry "
            "wired on host (Chunk 8) AND the legacy codesmell→SMELL family rename. "
            "See Chunk 7 handoff."
        )


__all__ = ["AnomalyQualityIssuesMetric"]
