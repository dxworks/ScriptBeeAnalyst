"""Quality-issues anomaly metric — v2 port.

Port of legacy ``src/enrichment/tagger/anomaly_quality_issues.py``
(~117 LOC). Re-emits each :class:`QualityIssue` row as an
``anomaly.codesmell.<Category>.<Rule>`` :class:`Trait` on the affected
file.

Per orchestrator decision (B4):

* **Trait taxonomy is data-driven** — names are discovered at runtime
  from the (category, rule) pairs observed on the input. The Insider /
  Sonar rule catalogue is much larger than what any one project
  happens to surface.
* **No severity translation** — ``severity`` and
  ``evidence["occurrence_count"]`` carry the raw firing count
  (Insider's ``value``; Sonar leaves ``1``). A later metric can
  introduce normalisation.
* **Family is ``SMELL``** — the legacy emitted ``family="codesmell"``
  string; Chunk 3 renamed the enum member to :attr:`TraitFamily.SMELL`.
  The trait *name* namespace keeps the legacy ``anomaly.codesmell.``
  prefix intact so existing agent prompts keep working.

Reads from the host: ``quality_issues`` (whole registry). The legacy
joined the issue's ``file_path`` against the git File registry to set
``evidence["git_matched"]``; v2 ports this by checking whether the
issue's typed ``file_ref`` resolves to a real :class:`File`.
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Trait, TraitFamily

if TYPE_CHECKING:
    from src.common.kernel import Graph


@METRICS.register
class AnomalyQualityIssuesMetric(Metric):
    name: ClassVar[str] = "anomaly.quality_issues"
    inputs: ClassVar[MetricInputs] = MetricInputs(
        source_kind=EntityKind.QUALITY_ISSUE
    )
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=["anomaly.codesmell.*"]
    )
    config_fields: ClassVar[list[str]] = []

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        issues = _safe_iter(getattr(graph, "quality_issues", None))
        if not issues:
            return

        # Pre-resolve the git File id set so we can tag
        # ``evidence["git_matched"]`` without re-scanning per issue.
        files = getattr(graph, "files", None)
        known_file_ids: set[str] = set()
        if files is not None:
            try:
                known_file_ids = {f.id for f in files}
            except TypeError:
                known_file_ids = set()

        # Bin by (file_ref, category, rule_id) — multiple Insider rows
        # with the same key sum into one trait with stacked
        # occurrence_count (mirrors the legacy "one Trait per
        # (category, rule) bin" rule).
        bins: dict[tuple[EntityRef, str, str], list[Any]] = defaultdict(list)
        for issue in issues:
            file_ref = getattr(issue, "file_ref", None)
            category = getattr(issue, "category", None) or ""
            rule_id = getattr(issue, "rule_id", None) or ""
            if file_ref is None or not category or not rule_id:
                continue
            bins[(file_ref, category, rule_id)].append(issue)

        for (file_ref, category, rule_id), records in bins.items():
            occurrence_count = sum(
                int(getattr(r, "occurrence_count", 1) or 0) for r in records
            )
            trait_name = _trait_name(category, rule_id)
            source_tool = (
                getattr(records[0], "source_tool", None) or "insider"
            )
            git_matched = file_ref.id in known_file_ids
            yield Trait(
                id=(
                    f"trait:{trait_name}:{file_ref.kind.value}/{file_ref.id}"
                ),
                target=file_ref,
                family=TraitFamily.SMELL,
                name=trait_name,
                severity=float(occurrence_count),
                evidence={
                    "category": category,
                    "rule_name": rule_id,
                    "occurrence_count": int(occurrence_count),
                    "record_count": len(records),
                    "basis": str(source_tool),
                    "git_matched": git_matched,
                },
            )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_iter(reg: Any) -> list[Any]:
    if reg is None:
        return []
    try:
        return list(reg)
    except TypeError:
        return []


def _trait_name(category: str, rule_name: str) -> str:
    """Build ``anomaly.codesmell.{Category}.{Rule}`` with whitespace stripped.

    Insider rule names contain spaces (e.g. ``"Stub Implementer"``); the
    trait namespace stays dot-separated and PascalCase for parity with
    the rest of the ``anomaly.*`` family. Sanitise both segments so
    dots / hyphens / spaces cannot leak into the namespace.
    """
    cat = _sanitise_segment(category)
    rule = _sanitise_segment(rule_name)
    return f"anomaly.codesmell.{cat}.{rule}"


def _sanitise_segment(segment: str) -> str:
    """Strip non-alphanumeric characters from a single trait-name segment.

    Preserves capitalisation (``"Stub Implementer"`` → ``"StubImplementer"``).
    Drops everything that is not a letter or digit so spaces, dots,
    hyphens, and other punctuation cannot leak into the dot-separated
    trait namespace.
    """
    return "".join(ch for ch in segment if ch.isalnum())


__all__ = ["AnomalyQualityIssuesMetric", "_trait_name"]
