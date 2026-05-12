"""Issue/PR classifiers metric — DEFERRED stub.

Port of legacy ``src/enrichment/tagger/issue_pr_classifiers.py``
(~209 LOC — biggest classifier tagger). Emits per-issue classifiers
(age, resolution, type) and per-PR classifiers (size, state,
review_intensity).

Issue side depends on ``resolved_status_categories`` config + age
bucketing against ``recent_cutoff``.

PR side depends on review counts (read from ``graph.reviews`` indexed by
``by_pull_request``) AND linked-commit churn (read from
``pr_file`` relations from Chunk-7's :class:`PrFileBuilder`). The
chunk-7 spec lists this as one of the bigger ports; deferred until the
v2 host wiring lands.

See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Union

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Classifier, Trait

if TYPE_CHECKING:
    from src.common.kernel import Graph


@METRICS.register
class IssuePRClassifierMetric(Metric):
    name: ClassVar[str] = "issue_pr.classifiers"
    inputs: ClassVar[MetricInputs] = MetricInputs()  # walks two registries
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_classifiers=[
            "issue.age",
            "issue.resolution",
            "pr.size",
            "pr.state",
            "pr.review_intensity",
        ]
    )
    config_fields: ClassVar[list[str]] = [
        "issue_age_buckets",
        "resolved_status_categories",
        "pr_size_xs_max",
        "pr_size_s_max",
        "pr_size_m_max",
        "pr_size_l_max",
        "review_intensity_light_max",
        "review_intensity_heavy_min",
    ]

    def compute(
        self, graph: "Graph", config: Any
    ) -> Iterable[Union[Classifier, Trait]]:
        raise NotImplementedError(
            "IssuePRClassifierMetric port deferred — depends on pr_file relations + "
            "by_pull_request review index + age bucketing. See Chunk 7 handoff."
        )


__all__ = ["IssuePRClassifierMetric"]
