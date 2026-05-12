"""Timezone anomaly metric — DEFERRED stub.

Port of legacy ``src/enrichment/tagger/anomaly_timezone.py`` (~131 LOC).
Emits ZoneCrossroad, ConcurrentZoneCrossroad traits in the ``COHESION``
family — based on UTC-offset distribution per file. Self-contained in
spirit (no external utils) but defers to keep chunk size manageable.
See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Trait

if TYPE_CHECKING:
    from src.common.kernel import Graph


@METRICS.register
class AnomalyTimezoneMetric(Metric):
    name: ClassVar[str] = "anomaly.timezone"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=[
            "anomaly.cohesion.ZoneCrossroad",
            "anomaly.cohesion.ConcurrentZoneCrossroad",
        ]
    )
    config_fields: ClassVar[list[str]] = [
        "zonecrossroad_min_zone_commits",
        "concurrent_zonecrossroad_strict_threshold",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        raise NotImplementedError(
            "AnomalyTimezoneMetric port deferred — see Chunk 7 handoff."
        )


__all__ = ["AnomalyTimezoneMetric"]
