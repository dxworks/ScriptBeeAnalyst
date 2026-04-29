"""Author-level mandatory classifiers: activity, seniority.

Works on GitAccount (raw git authors). A Phase-2 tagger could target
UnifiedUser via `graph_data['users']` and aggregate across identities.
"""
from __future__ import annotations

from typing import Iterable, Optional

from src.enrichment.models import EntityTags
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import Tagger, TaggingContext


class AuthorClassifiersTagger:

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        cfg = ctx.config
        out: list[EntityTags] = []

        for account in git.account_registry.all:
            dates = [ensure_aware(getattr(c, "author_date", None)) for c in account.commits or []]
            dates = [d for d in dates if d is not None]
            if not dates:
                continue
            first = min(dates)
            last = max(dates)

            classifiers: dict[str, str] = {}

            # activity — active if last commit falls inside recent window
            if ctx.recent_cutoff is None or last >= ctx.recent_cutoff:
                classifiers["activity"] = "active"
            else:
                classifiers["activity"] = "idle"

            # seniority — span between first and last commit
            span_days = (last - first).days
            classifiers["seniority"] = _seniority_bucket(span_days, cfg)

            out.append(EntityTags(
                entity_kind="author",
                entity_id=account.id,
                classifiers=classifiers,
            ))

        return out


def _seniority_bucket(span_days: int, cfg) -> str:
    if span_days <= cfg.newcomer_max_days:
        return "newcomer"
    if span_days <= cfg.established_max_days:
        return "established"
    if span_days <= cfg.senior_max_days:
        return "senior"
    return "veteran"
