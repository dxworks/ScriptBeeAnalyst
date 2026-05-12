"""Cohesion anomaly metric — DEFERRED stub.

Port of legacy ``src/enrichment/tagger/anomaly_cohesion.py`` (~279 LOC).
Emits Hibernator, Awakening, Erosion, Flicker, FrequentChanger traits in
the ``COHESION`` family. The port is gnarly because it depends on
``file_trait_utils``' time-bucketed-churn primitives — those need a v2
port too before this metric can land. See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Trait

if TYPE_CHECKING:
    from src.common.kernel import Graph


@METRICS.register
class AnomalyCohesionMetric(Metric):
    name: ClassVar[str] = "anomaly.cohesion"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=[
            "anomaly.cohesion.Hibernator",
            "anomaly.cohesion.Awakening",
            "anomaly.cohesion.Erosion",
            "anomaly.cohesion.Flicker",
            "anomaly.cohesion.FrequentChanger",
        ]
    )
    config_fields: ClassVar[list[str]] = [
        "hibernator_min_lifetime_commits",
        "awakening_min_dormant_weeks",
        "awakening_recent_commits_min",
        "erosion_window_weeks",
        "erosion_trend_max",
        "flicker_cv_min",
        "flicker_min_recent_commits",
        "frequent_changer_lifetime_min",
        "frequent_changer_recent_min",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        raise NotImplementedError(
            "AnomalyCohesionMetric port deferred — depends on file_trait_utils "
            "v2 port. See Chunk 7 handoff 'Deferred ports'."
        )


__all__ = ["AnomalyCohesionMetric"]
