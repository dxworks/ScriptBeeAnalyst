"""anomaly.review.* — PR-level traits.

StalledReview: an open PR that has been open beyond a threshold and either has
no reviews at all OR its last review submittedAt is older than the same
threshold (relative to the project anchor — mirrors `tasks_bottleneck`).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

from src.enrichment.models import EntityTags
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext, make_trait


class StalledReviewTagger:

    TRAITS = [
        {"name": "anomaly.review.StalledReview", "entity": "pr", "family": "review"},
    ]

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        github = ctx.graph_data.get("github")
        if github is None:
            return []

        cfg = ctx.config
        anchor = ctx.anchor_date or datetime.now(tz=timezone.utc)
        threshold = cfg.stalled_review_open_days_min
        out: list[EntityTags] = []

        for pr in github.pull_request_registry.all:
            if (pr.state or "").lower() != "open":
                continue

            created = ensure_aware(getattr(pr, "createdAt", None))
            if created is None:
                continue
            pr_open_days = (anchor - created).days
            if pr_open_days < threshold:
                continue

            reviews = list(getattr(pr, "reviews", None) or [])
            review_count = len(reviews)

            submitted_dates = [
                ensure_aware(getattr(r, "submittedAt", None))
                for r in reviews
            ]
            submitted_dates = [d for d in submitted_dates if d is not None]

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

            out.append(EntityTags(
                entity_kind="pr",
                entity_id=str(pr.number),
                traits=[make_trait(
                    "anomaly.review.StalledReview",
                    family="review",
                    severity=float(pr_open_days),
                    pr_open_days=pr_open_days,
                    days_since_last_review=days_since_last_review,
                    review_count=review_count,
                    review_states_summary=states_summary,
                    threshold=threshold,
                )],
            ))

        return out
