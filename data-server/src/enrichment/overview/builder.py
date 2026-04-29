"""Shared overview-table plumbing: rate-per-week, nature-mix, trend%.

dx's overview tables all share the same `(lifetime_value, recent_value,
trend_percent)` triple per cell — this module holds the primitives so each
per-table builder reads as a list of cell specs rather than plumbing.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from src.enrichment.models import OverviewCell
from src.enrichment.recent_window import ensure_aware, trend_percent


def rate_per_week(items: Iterable, window_days: Optional[int]) -> Optional[float]:
    """Count / (window_days / 7). Returns None if the window is unknown."""
    if window_days is None or window_days <= 0:
        return None
    count = sum(1 for _ in items)
    weeks = window_days / 7.0
    if weeks == 0:
        return None
    return round(count / weeks, 3)


def span_days(items: Iterable, date_attr: str = "author_date") -> Optional[int]:
    dates = [ensure_aware(getattr(i, date_attr, None)) for i in items]
    dates = [d for d in dates if d is not None]
    if len(dates) < 2:
        return None
    return max(1, (max(dates) - min(dates)).days)


def percentage(n: int, d: int) -> Optional[float]:
    if d == 0:
        return None
    return round(100.0 * n / d, 2)


def rate_cell(
    lifetime_items: list,
    recent_items: list,
    lifetime_days: Optional[int],
    recent_days: Optional[int],
) -> OverviewCell:
    """Build a rate-per-week cell with trend%."""
    lt = rate_per_week(lifetime_items, lifetime_days)
    rc = rate_per_week(recent_items, recent_days)
    return OverviewCell(
        lifetime_value=lt,
        recent_value=rc,
        trend_percent=trend_percent(lt, rc),
    )


def share_cell(
    lifetime_items: list,
    recent_items: list,
    predicate,
) -> OverviewCell:
    """Build a %-share cell, lifetime vs recent, with trend%."""
    def share(items):
        if not items:
            return None
        matched = sum(1 for i in items if predicate(i))
        return percentage(matched, len(items))

    lt = share(lifetime_items)
    rc = share(recent_items)
    return OverviewCell(
        lifetime_value=lt,
        recent_value=rc,
        trend_percent=trend_percent(lt, rc),
    )


def constant_cell(value) -> OverviewCell:
    return OverviewCell(lifetime_value=value, recent_value=value, trend_percent=None)
