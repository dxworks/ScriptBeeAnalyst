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
    """Per-issue mandatory classifiers: status, type, resolution, age_bucket.

    `status` and `type` carry the native JIRA values (last status / last type
    in the change history) — vocabulary varies per project. `resolution` collapses
    statuses into open/resolved using `cfg.resolved_status_categories`.
    `age_bucket` groups (anchor − createdAt) days using `cfg.issue_age_buckets`;
    for resolved issues the upper bound is `updatedAt`, not the anchor.
    """

    CLASSIFIERS = [
        # status / type vocabulary is project-specific (native JIRA names).
        {"slot": "status",     "entity": "issue", "values": []},
        {"slot": "type",       "entity": "issue", "values": []},
        {"slot": "resolution", "entity": "issue", "values": ["open", "resolved"]},
        {"slot": "age_bucket", "entity": "issue",
         "values": ["<1w", "1-4w", "1-3m", "3-12m", ">1y"]},
    ]

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
    """Per-PR mandatory classifiers: state, size, review_intensity.

    `state` carries the native GitHub value (open / merged / closed). `size`
    buckets `(changedFiles + linked-commit churn)` using the four `cfg.pr_size_*_max`
    thresholds (XS through XL). `review_intensity` counts review submissions in
    {APPROVED, COMMENTED, CHANGES_REQUESTED, PENDING} (DISMISSED excluded) and
    buckets via `cfg.review_intensity_light_max` and `cfg.review_intensity_heavy_min`.
    """

    CLASSIFIERS = [
        {"slot": "state",            "entity": "pr",
         # Native GitHub vocabulary; the canonical three are listed for reference.
         "values": ["open", "merged", "closed"]},
        {"slot": "size",             "entity": "pr",
         "values": ["XS", "S", "M", "L", "XL"]},
        {"slot": "review_intensity", "entity": "pr",
         "values": ["none", "light", "moderate", "heavy"]},
    ]

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
            classifiers["review_intensity"] = _review_intensity(pr, cfg)

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


# Review states that count toward review_intensity. DISMISSED is excluded
# because a dismissed review no longer represents reviewer engagement.
_REVIEW_INTENSITY_COUNTED_STATES = {"APPROVED", "COMMENTED", "CHANGES_REQUESTED", "PENDING"}


def _review_intensity(pr, cfg) -> str:
    reviews = getattr(pr, "reviews", None) or []
    count = sum(
        1 for r in reviews
        if (getattr(r, "state", None) or "").upper() in _REVIEW_INTENSITY_COUNTED_STATES
    )
    if count == 0:
        return "none"
    if count <= cfg.review_intensity_light_max:
        return "light"
    if count < cfg.review_intensity_heavy_min:
        return "moderate"
    return "heavy"


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
