"""PR-lifecycle overview — v2 port (Chunk 17).

Port of legacy ``src/enrichment/overview/pr_lifecycle_table.py``. Computes
project-level pull-request review/merge timing + the Chunk-16 PR
classifier-bucket distributions.

Reads:

* ``graph.pull_requests`` + ``graph.reviews`` (with ``by_pull_request``
  index) for merge-vs-first-review timing math.
* ``graph.classifiers`` (dimensions ``pr.state`` / ``pr.size`` /
  ``pr.review_intensity``) emitted by :class:`IssuePRClassifierMetric`
  for per-bucket counts.
* ``graph.traits`` (name ``anomaly.review.StalledReview``) emitted by
  :class:`PRTraitsMetric` for the stalled-review count.

Columns (lifetime + recent + trend% on rate-style cells):

  review_turnaround_hours, total_prs, stalled_review_count,
  pct_size_xs, pct_size_s, pct_size_m, pct_size_l, pct_size_xl,
  pct_review_intensity_none, pct_review_intensity_light,
  pct_review_intensity_moderate, pct_review_intensity_heavy.

Rows: a single ``(project)`` row (per-component split deferred until
needed; mirrors the legacy table's deliberate single-row shape).
"""
from __future__ import annotations

from statistics import mean
from typing import TYPE_CHECKING, Any, ClassVar, Optional

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.overviews.models import (
    OverviewCell,
    OverviewRow,
    OverviewTable,
    OverviewTableBuilder,
)
from src.enrichment.overviews.registries import OVERVIEWS
from src.enrichment.recent_window import ensure_aware, trend_percent

if TYPE_CHECKING:
    from src.common.kernel import Graph


_SIZE_BUCKETS: tuple[str, ...] = ("XS", "S", "M", "L", "XL")
_INTENSITY_BUCKETS: tuple[str, ...] = ("none", "light", "moderate", "heavy")

COLUMNS: list[str] = (
    [
        "review_turnaround_hours",
        "total_prs",
        "stalled_review_count",
    ]
    + [f"pct_size_{b.lower()}" for b in _SIZE_BUCKETS]
    + [f"pct_review_intensity_{b}" for b in _INTENSITY_BUCKETS]
)

_PR_SIZE_DIM = "pr.size"
_PR_INTENSITY_DIM = "pr.review_intensity"
_STALLED_TRAIT = "anomaly.review.StalledReview"


@OVERVIEWS.register
class PrLifecycleTableBuilder(OverviewTableBuilder):
    """One project-level row summarising PR lifecycle metrics."""

    name: ClassVar[str] = "pr_lifecycle"

    def build(self, graph: "Graph", config: Any) -> OverviewTable:
        prs_reg = getattr(graph, "pull_requests", None)
        if prs_reg is None:
            return OverviewTable(
                name=self.name, entity_kind="project",
                columns=COLUMNS, rows=[],
            )
        try:
            prs = list(prs_reg)
        except TypeError:
            prs = []

        cutoff = _resolve_recent_cutoff(graph)
        reviews_by_pr = _reviews_by_pr_index(graph)
        size_by_pr = _classifier_by_pr_id(graph, _PR_SIZE_DIM)
        intensity_by_pr = _classifier_by_pr_id(graph, _PR_INTENSITY_DIM)
        stalled_pr_count = _trait_target_count(
            graph, _STALLED_TRAIT, EntityKind.PULL_REQUEST,
        )

        # --- turnaround math -------------------------------------------------
        lifetime_turnaround: list[float] = []
        recent_turnaround: list[float] = []
        for pr in prs:
            merged_at = ensure_aware(getattr(pr, "merged_at", None))
            if merged_at is None:
                continue
            review_dates: list[Any] = []
            for r in reviews_by_pr(pr.ref()):
                submitted = ensure_aware(getattr(r, "submitted_at", None))
                if submitted is not None:
                    review_dates.append(submitted)
            if not review_dates:
                continue
            first_review = min(review_dates)
            hours = (merged_at - first_review).total_seconds() / 3600.0
            lifetime_turnaround.append(hours)
            if cutoff is not None and merged_at >= cutoff:
                recent_turnaround.append(hours)

        lifetime_mean = (
            round(mean(lifetime_turnaround), 2) if lifetime_turnaround else None
        )
        recent_mean = (
            round(mean(recent_turnaround), 2) if recent_turnaround else None
        )

        cells: dict[str, OverviewCell] = {}
        cells["review_turnaround_hours"] = OverviewCell(
            lifetime_value=lifetime_mean,
            recent_value=recent_mean,
            trend_percent=trend_percent(lifetime_mean, recent_mean),
        )

        # --- counts ---------------------------------------------------------
        total_prs = len(prs)
        cells["total_prs"] = OverviewCell(
            lifetime_value=total_prs,
            recent_value=total_prs,
            trend_percent=None,
        )
        cells["stalled_review_count"] = OverviewCell(
            lifetime_value=stalled_pr_count,
            recent_value=stalled_pr_count,
            trend_percent=None,
        )

        # --- bucket distributions ------------------------------------------
        for bucket in _SIZE_BUCKETS:
            cells[f"pct_size_{bucket.lower()}"] = _share_cell(
                prs,
                lambda pr, b=bucket: size_by_pr.get(pr.id) == b,
            )
        for bucket in _INTENSITY_BUCKETS:
            cells[f"pct_review_intensity_{bucket}"] = _share_cell(
                prs,
                lambda pr, b=bucket: intensity_by_pr.get(pr.id) == b,
            )

        row = OverviewRow(entity_id="(project)", cells=cells)
        return OverviewTable(
            name=self.name,
            entity_kind="project",
            columns=COLUMNS,
            rows=[row],
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _reviews_by_pr_index(graph: Any):
    reviews = getattr(graph, "reviews", None)
    if reviews is None:
        return lambda _ref: []
    by_pr = getattr(reviews, "by_pull_request", None)
    if by_pr is not None:
        return lambda pr_ref: by_pr[pr_ref]

    def scan(pr_ref):
        return [r for r in reviews if getattr(r, "pull_request_ref", None) == pr_ref]

    return scan


def _classifier_by_pr_id(graph: Any, dimension: str) -> dict[str, str]:
    classifiers = getattr(graph, "classifiers", None)
    if classifiers is None:
        return {}
    of_dimension = getattr(classifiers, "of_dimension", None)
    if of_dimension is None:
        return {}
    out: dict[str, str] = {}
    for cls_obj in of_dimension(dimension):
        target: EntityRef = cls_obj.target
        if target.kind != EntityKind.PULL_REQUEST:
            continue
        out[target.id] = cls_obj.value
    return out


def _trait_target_count(
    graph: Any, trait_name: str, target_kind: EntityKind,
) -> int:
    traits = getattr(graph, "traits", None)
    if traits is None:
        return 0
    of_name = getattr(traits, "of_name", None)
    if of_name is None:
        return 0
    count = 0
    for t in of_name(trait_name):
        target: EntityRef = t.target
        if target.kind == target_kind:
            count += 1
    return count


def _share_cell(prs: list[Any], predicate) -> OverviewCell:
    def _share(items: list[Any]) -> Optional[float]:
        if not items:
            return None
        matched = sum(1 for i in items if predicate(i))
        return round(100.0 * matched / len(items), 2)

    value = _share(prs)
    return OverviewCell(
        lifetime_value=value,
        recent_value=value,
        trend_percent=None,
    )


def _resolve_recent_cutoff(graph: Any) -> Optional[Any]:
    explicit = getattr(graph, "recent_cutoff", None)
    if explicit is not None:
        return ensure_aware(explicit)
    return None


__all__ = ["PrLifecycleTableBuilder", "COLUMNS"]
