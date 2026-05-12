"""Testing anomaly metric — DEFERRED stub.

Port of legacy ``src/enrichment/tagger/anomaly_testing.py`` (~161 LOC).
Emits TestOrphan, RefactoringMagnet, Supernova traits in the
``TESTING`` family. Depends on file_trait_utils + cochange relations.
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
class AnomalyTestingMetric(Metric):
    name: ClassVar[str] = "anomaly.testing"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=[
            "anomaly.testing.TestOrphan",
            "anomaly.testing.RefactoringMagnet",
            "anomaly.testing.Supernova",
        ]
    )
    config_fields: ClassVar[list[str]] = [
        "test_orphan_max_cochange_test_count",
        "test_orphan_min_commits",
        "refactoring_magnet_min_commits",
        "supernova_net_churn_min",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        raise NotImplementedError(
            "AnomalyTestingMetric port deferred — depends on file_trait_utils + "
            "cochange relations + file-role classifier output. See Chunk 7 handoff."
        )


__all__ = ["AnomalyTestingMetric"]
