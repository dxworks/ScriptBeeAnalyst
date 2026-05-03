"""PR lifecycle overview — project-level review/merge timing.

Rows: just `(project)` for now (per-component split deferred until needed).

Columns (lifetime + recent + trend%):
  - review_turnaround_hours — mean of (mergedAt - first review submittedAt) in
    hours over merged PRs that have at least one review with a submittedAt.
"""
from __future__ import annotations

from statistics import mean
from typing import Optional

from src.enrichment.models import OverviewCell, OverviewRow, OverviewTable
from src.enrichment.recent_window import ensure_aware, trend_percent
from src.enrichment.tagger.base import TaggingContext


COLUMNS = ["review_turnaround_hours"]


class PullRequestLifecycleTableBuilder:

    NAME = "pr_lifecycle"
    ENTITY_KIND = "project"

    def build(self, ctx: TaggingContext, tags_by_entity: dict) -> OverviewTable:
        github = ctx.graph_data.get("github")
        rows: list[OverviewRow] = []
        if github is None:
            return OverviewTable(name=self.NAME, entity_kind="project", columns=COLUMNS, rows=rows)

        cutoff = ctx.recent_cutoff

        lifetime_values: list[float] = []
        recent_values: list[float] = []
        for pr in github.pull_request_registry.all:
            merged_at = ensure_aware(getattr(pr, "mergedAt", None))
            if merged_at is None:
                continue
            review_dates = [
                ensure_aware(getattr(r, "submittedAt", None))
                for r in (getattr(pr, "reviews", None) or [])
            ]
            review_dates = [d for d in review_dates if d is not None]
            if not review_dates:
                continue
            first_review = min(review_dates)
            hours = (merged_at - first_review).total_seconds() / 3600.0
            lifetime_values.append(hours)
            if cutoff is not None and merged_at >= cutoff:
                recent_values.append(hours)

        lifetime_mean = round(mean(lifetime_values), 2) if lifetime_values else None
        recent_mean = round(mean(recent_values), 2) if recent_values else None

        rows.append(OverviewRow(
            entity_id="(project)",
            cells={
                "review_turnaround_hours": OverviewCell(
                    lifetime_value=lifetime_mean,
                    recent_value=recent_mean,
                    trend_percent=trend_percent(lifetime_mean, recent_mean),
                ),
            },
        ))

        return OverviewTable(
            name=self.NAME,
            entity_kind="project",
            columns=COLUMNS,
            rows=rows,
        )
