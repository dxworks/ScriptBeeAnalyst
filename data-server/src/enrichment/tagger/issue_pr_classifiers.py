"""Issue and PR mandatory classifiers.

Issue: status, type, resolution (resolved/open), age_bucket.
PR:    state (native), size (XS/S/M/L/XL).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from src.enrichment.models import EntityTags
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext


class IssueClassifiersTagger:

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        jira = ctx.graph_data.get("jira")
        if jira is None:
            return []

        cfg = ctx.config
        anchor = ctx.anchor_date or datetime.now(tz=timezone.utc)
        out: list[EntityTags] = []

        for issue in jira.issue_registry.all:
            classifiers: dict[str, str] = {}

            status_name = _latest_status_name(issue)
            if status_name:
                classifiers["status"] = status_name

            type_name = _latest_type_name(issue)
            if type_name:
                classifiers["type"] = type_name

            classifiers["resolution"] = _resolution(issue, cfg.resolved_status_categories)

            age_days = _age_in_days(issue, anchor, cfg.resolved_status_categories)
            if age_days is not None:
                classifiers["age_bucket"] = _age_bucket(age_days, cfg.issue_age_buckets)

            out.append(EntityTags(
                entity_kind="issue",
                entity_id=issue.key,
                classifiers=classifiers,
            ))

        return out


class PullRequestClassifiersTagger:

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        github = ctx.graph_data.get("github")
        if github is None:
            return []

        cfg = ctx.config
        out: list[EntityTags] = []

        for pr in github.pull_request_registry.all:
            classifiers: dict[str, str] = {}

            if pr.state:
                classifiers["state"] = pr.state

            classifiers["size"] = _pr_size(pr, cfg)

            out.append(EntityTags(
                entity_kind="pr",
                entity_id=str(pr.number),
                classifiers=classifiers,
            ))

        return out


# ── helpers ────────────────────────────────────────────────────────────────────

def _latest_status_name(issue) -> Optional[str]:
    statuses = getattr(issue, "issue_statuses", None) or []
    if not statuses:
        return None
    return getattr(statuses[-1], "name", None)


def _latest_type_name(issue) -> Optional[str]:
    types = getattr(issue, "issue_types", None) or []
    if not types:
        return None
    return getattr(types[-1], "name", None)


def _resolution(issue, resolved_categories: tuple[str, ...]) -> str:
    statuses = getattr(issue, "issue_statuses", None) or []
    if not statuses:
        return "open"
    latest = statuses[-1]
    cat = getattr(latest, "issue_status_categories", None)
    cat_name = getattr(cat, "name", None) or getattr(cat, "key", None) or ""
    if cat_name and cat_name.strip().lower() in resolved_categories:
        return "resolved"
    name = (getattr(latest, "name", None) or "").strip().lower()
    if name in resolved_categories:
        return "resolved"
    return "open"


def _age_in_days(issue, anchor: datetime, resolved_categories: tuple[str, ...]) -> Optional[int]:
    created = ensure_aware(getattr(issue, "createdAt", None))
    if created is None:
        return None
    end = anchor
    statuses = getattr(issue, "issue_statuses", None) or []
    if statuses:
        cat = getattr(statuses[-1], "issue_status_categories", None)
        cat_name = (getattr(cat, "name", None) or getattr(cat, "key", None) or "").strip().lower()
        if cat_name in resolved_categories:
            updated = ensure_aware(getattr(issue, "updatedAt", None))
            if updated is not None:
                end = updated
    delta = end - created
    return max(0, delta.days)


def _age_bucket(age_days: int, buckets: list[tuple[str, int]]) -> str:
    for label, max_days in buckets:
        if age_days <= max_days:
            return label
    return buckets[-1][0] if buckets else "unknown"


def _pr_size(pr, cfg) -> str:
    score = pr.changedFiles or 0
    for ghc in getattr(pr, "git_hub_commits", None) or []:
        score += getattr(ghc, "changedFiles", 0) or 0
    for c in getattr(pr, "git_commits", None) or []:
        for change in getattr(c, "changes", None) or []:
            for hunk in getattr(change, "hunks", None) or []:
                score += len(getattr(hunk, "added_lines", []) or [])
                score += len(getattr(hunk, "deleted_lines", []) or [])

    if score <= cfg.pr_size_xs_max:
        return "XS"
    if score <= cfg.pr_size_s_max:
        return "S"
    if score <= cfg.pr_size_m_max:
        return "M"
    if score <= cfg.pr_size_l_max:
        return "L"
    return "XL"
