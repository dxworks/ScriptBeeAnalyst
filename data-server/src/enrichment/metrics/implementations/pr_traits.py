"""PR traits metric — v2 port (Chunk 16).

Port of legacy ``src/enrichment/tagger/pr_traits.py`` (~82 LOC). Emits a
single :class:`Trait` in the :attr:`TraitFamily.REVIEW` family:

* ``anomaly.review.StalledReview`` — an open :class:`PullRequest` that
  has been open beyond ``cfg.stalled_review_open_days_min`` days and
  either has no reviews at all OR whose most-recent review's
  ``submitted_at`` is older than that same threshold.

Reads:

* ``graph.pull_requests``            — the PR population
                                       (filtered by ``state == "open"``).
* ``graph.reviews.by_pull_request``  — review lookup per PR.
* ``graph.anchor_date`` (optional)   — anchor for age math (legacy
                                       test-stub convention); falls back
                                       to wall-clock now (UTC).

The metric is robust against PRs missing ``created_at`` (skipped) or
reviews missing ``submitted_at`` (excluded from the recency check, but
still counted in ``review_count`` / ``review_states_summary``).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Optional

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tags import Trait, TraitFamily

if TYPE_CHECKING:
    from src.common.kernel import Graph


_TRAIT_STALLED_REVIEW = "anomaly.review.StalledReview"

_DEFAULT_STALLED_REVIEW_OPEN_DAYS_MIN = 14


@METRICS.register
class PRTraitsMetric(Metric):
    name: ClassVar[str] = "pr.traits"
    inputs: ClassVar[MetricInputs] = MetricInputs(
        source_kind=EntityKind.PULL_REQUEST
    )
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=[_TRAIT_STALLED_REVIEW]
    )
    config_fields: ClassVar[list[str]] = ["stalled_review_open_days_min"]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        prs = _safe_iter(getattr(graph, "pull_requests", None))
        if not prs:
            return

        threshold = int(_config_field(
            config, "stalled_review_open_days_min",
            _DEFAULT_STALLED_REVIEW_OPEN_DAYS_MIN,
        ))
        anchor = _anchor_now(graph)
        reviews_for_pr = _reviews_by_pr_lookup(graph)

        for pr in prs:
            if (getattr(pr, "state", None) or "").lower() != "open":
                continue

            created = ensure_aware(getattr(pr, "created_at", None))
            if created is None:
                continue

            pr_open_days = (anchor - created).days
            if pr_open_days < threshold:
                continue

            pr_ref = pr.ref()
            reviews = list(reviews_for_pr(pr_ref))
            review_count = len(reviews)

            submitted_dates: list[datetime] = []
            for review in reviews:
                d = ensure_aware(getattr(review, "submitted_at", None))
                if d is not None:
                    submitted_dates.append(d)

            days_since_last_review: Optional[int]
            if submitted_dates:
                last_review_at = max(submitted_dates)
                days_since_last_review = (anchor - last_review_at).days
                if days_since_last_review < threshold:
                    continue
            else:
                days_since_last_review = None

            states_summary = dict(Counter(
                (getattr(r, "state", None) or "UNKNOWN").upper()
                for r in reviews
            ))

            yield Trait(
                id=f"trait:{_TRAIT_STALLED_REVIEW}:{pr_ref.kind.value}/{pr_ref.id}",
                target=pr_ref,
                family=TraitFamily.REVIEW,
                name=_TRAIT_STALLED_REVIEW,
                severity=float(pr_open_days),
                evidence=_build_evidence(
                    pr_open_days=pr_open_days,
                    days_since_last_review=days_since_last_review,
                    review_count=review_count,
                    states_summary=states_summary,
                    threshold=threshold,
                ),
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


def _config_field(config: Any, field: str, default: Any) -> Any:
    if config is None:
        return default
    return getattr(config, field, default)


def _anchor_now(graph: Any) -> datetime:
    """Anchor for PR-age math.

    Honours an explicit ``graph.anchor_date`` when present (legacy
    test-stub convention) and otherwise falls back to wall-clock now
    (UTC). Mirrors :func:`anomaly_structuring._anchor_now`.
    """
    explicit = getattr(graph, "anchor_date", None)
    if explicit is not None:
        d = ensure_aware(explicit)
        if d is not None:
            return d
    return datetime.now(timezone.utc)


def _reviews_by_pr_lookup(graph: Any):
    reviews = getattr(graph, "reviews", None)
    if reviews is None:
        return lambda _ref: ()
    by_pr = getattr(reviews, "by_pull_request", None)
    if by_pr is not None:
        return lambda pr_ref: by_pr[pr_ref]
    return lambda pr_ref: tuple(
        r for r in reviews if getattr(r, "pull_request_ref", None) == pr_ref
    )


def _build_evidence(
    *,
    pr_open_days: int,
    days_since_last_review: Optional[int],
    review_count: int,
    states_summary: dict[str, int],
    threshold: int,
) -> dict:
    """Pack the StalledReview evidence dict (typed-evidence safe).

    ``days_since_last_review`` is allowed to be ``None`` per
    ``EvidenceValue`` (it accepts ``None`` via the lack of an entry rather
    than a ``null`` literal). We surface ``-1`` as the "no reviews" sentinel
    so the evidence shape stays a homogeneous int-valued dict — matching
    the trait-evidence convention enforced elsewhere.
    """
    evidence: dict[str, Any] = {
        "pr_open_days": int(pr_open_days),
        "review_count": int(review_count),
        "review_states_summary": {k: int(v) for k, v in states_summary.items()},
        "threshold": int(threshold),
    }
    if days_since_last_review is None:
        evidence["days_since_last_review"] = -1
        evidence["had_reviews"] = False
    else:
        evidence["days_since_last_review"] = int(days_since_last_review)
        evidence["had_reviews"] = True
    return evidence


__all__ = ["PRTraitsMetric"]
