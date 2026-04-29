"""Time-window helpers. Mirrors dx's `Moment`+`startOfRecentTime` pattern.

dx computes lifetime/recent/trend% from the most-recent commit date as its
reference point, not wall-clock now — that matches how analysts reason about
a checked-out snapshot. We do the same here so pickled graphs from months ago
still produce meaningful "recent" windows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional


def latest_commit_date(commits: Iterable) -> Optional[datetime]:
    latest: Optional[datetime] = None
    for c in commits:
        d = getattr(c, "author_date", None) or getattr(c, "committer_date", None)
        if d is None:
            continue
        if latest is None or d > latest:
            latest = d
    return latest


def recent_cutoff(anchor: Optional[datetime], window_days: int) -> Optional[datetime]:
    """Return the start of the recent window, or None if no anchor is known."""
    if anchor is None:
        return None
    return anchor - timedelta(days=window_days)


def split_by_window(items: Iterable, date_attr: str, cutoff: Optional[datetime]):
    """Return (lifetime, recent) lists. If cutoff is None, recent == lifetime.

    `date_attr` is the attribute name on each item that carries the datetime
    (e.g. 'author_date').
    """
    lifetime = list(items)
    if cutoff is None:
        return lifetime, list(lifetime)
    recent = [i for i in lifetime if getattr(i, date_attr, None) and getattr(i, date_attr) >= cutoff]
    return lifetime, recent


def trend_percent(lifetime_value: Optional[float], recent_value: Optional[float]) -> Optional[float]:
    """Normalised delta in percent: (recent - lifetime_rate) / lifetime_rate.

    dx shows this as a single signed percentage. We intentionally compare raw
    values so caller can decide the normalisation (per-day, per-week, etc.) —
    use this helper on rates, not on absolute counts.

    Returns None when undefined (no baseline).
    """
    if lifetime_value is None or recent_value is None:
        return None
    if lifetime_value == 0:
        return None
    return round(((recent_value - lifetime_value) / lifetime_value) * 100.0, 2)


def ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce naive datetimes to UTC — graph entities mix tz-aware and tz-naive."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
