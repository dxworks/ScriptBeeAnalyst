"""Knowledge anomaly metric — DEFERRED stub.

Port of legacy ``src/enrichment/tagger/anomaly_knowledge.py`` (~334 LOC,
the largest tagger). Emits Orphan, BusFactor1, SharedKnowledge,
Accumulator, OwnerChurn, PolarisedOwnership, Solitaire, TeamChurn,
WeakOwnership, OrphanCausers traits. Depends on ``file_trait_utils``
v2 port AND the author-classifier output (Solitaire reads
``activity`` classifiers). See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Trait

if TYPE_CHECKING:
    from src.common.kernel import Graph


@METRICS.register
class AnomalyKnowledgeMetric(Metric):
    name: ClassVar[str] = "anomaly.knowledge"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=[
            "anomaly.knowledge.Orphan",
            "anomaly.knowledge.BusFactor1",
            "anomaly.knowledge.SharedKnowledge",
            "anomaly.knowledge.Accumulator",
            "anomaly.knowledge.OwnerChurn",
            "anomaly.knowledge.PolarisedOwnership",
            "anomaly.knowledge.Solitaire",
            "anomaly.knowledge.TeamChurn",
            "anomaly.knowledge.WeakOwnership",
            "anomaly.knowledge.OrphanCausers",
        ]
    )
    config_fields: ClassVar[list[str]] = [
        "hermit_dominance_ratio",
        "busfactor1_min_distinct_authors",
        "shared_knowledge_entropy_min",
        "shared_knowledge_min_distinct_authors",
        "accumulator_bucket_weeks",
        "accumulator_min_windows",
        "owner_churn_dominance_threshold",
        "polarised_top_share",
        "polarised_min_authors",
        "solitaire_min_lifetime_commits",
        "team_churn_set_change_ratio",
        "weak_owner_max_share",
        "weak_owner_min_active_authors",
        "orphancauser_min_orphan_files",
        "orphancauser_min_lifetime_commits",
        "orphancauser_orphan_sample_cap",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        raise NotImplementedError(
            "AnomalyKnowledgeMetric port deferred — largest tagger; depends on "
            "file_trait_utils v2 port + author activity classifier output. "
            "See Chunk 7 handoff."
        )


__all__ = ["AnomalyKnowledgeMetric"]
