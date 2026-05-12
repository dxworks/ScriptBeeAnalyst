"""PR traits metric — DEFERRED stub.

Port of legacy ``src/enrichment/tagger/pr_traits.py`` (~82 LOC). Emits
``anomaly.review.StalledReview`` and related PR-review traits in the
``REVIEW`` family. Depends on review timestamps + open-PR age. Deferred
until the v2 host wiring lands.

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
class PRTraitsMetric(Metric):
    name: ClassVar[str] = "pr.traits"
    inputs: ClassVar[MetricInputs] = MetricInputs(
        source_kind=EntityKind.PULL_REQUEST
    )
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=["anomaly.review.StalledReview"]
    )
    config_fields: ClassVar[list[str]] = ["stalled_review_open_days_min"]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        raise NotImplementedError(
            "PRTraitsMetric port deferred — needs review timestamps + open-PR "
            "age computation. See Chunk 7 handoff."
        )


__all__ = ["PRTraitsMetric"]
