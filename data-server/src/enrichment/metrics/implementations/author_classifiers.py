"""Author classifiers metric — DEFERRED stub.

Port of legacy ``src/enrichment/tagger/author_classifiers.py`` (~76 LOC).
Emits ``activity`` (active/idle), ``seniority`` (newcomer/established/senior/veteran)
classifiers per :class:`GitAccount`. Depends on a per-author last-commit
date roll-up. See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Classifier

if TYPE_CHECKING:
    from src.common.kernel import Graph


@METRICS.register
class AuthorClassifierMetric(Metric):
    name: ClassVar[str] = "author.classifiers"
    inputs: ClassVar[MetricInputs] = MetricInputs(
        source_kind=EntityKind.GIT_ACCOUNT
    )
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_classifiers=["activity", "seniority"]
    )
    config_fields: ClassVar[list[str]] = [
        "recent_window_days",
        "newcomer_max_days",
        "established_max_days",
        "senior_max_days",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Classifier]:
        raise NotImplementedError(
            "AuthorClassifierMetric port deferred — depends on per-author "
            "first/last commit date aggregation. See Chunk 7 handoff."
        )


__all__ = ["AuthorClassifierMetric"]
