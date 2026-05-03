"""Shared per-file helpers used by the A2.1 file-level anomaly traits.

`_author_churn` previously lived in `anomaly_knowledge`; it is hoisted here so
both the knowledge and cohesion taggers can reuse it without circular imports.
The bucketing helpers feed Accumulator/Erosion/Flicker; `_active_author_churn`
backs WeakOwnership.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from src.enrichment.recent_window import ensure_aware


def _files_touched_by_author(account) -> set:
    """Distinct File objects an author ever touched (any change)."""
    files = set()
    for commit in getattr(account, "commits", None) or []:
        for change in getattr(commit, "changes", None) or []:
            f = getattr(change, "file", None)
            if f is not None:
                files.add(f)
    return files


def _change_churn(change) -> int:
    added = sum(len(getattr(h, "added_lines", []) or []) for h in (getattr(change, "hunks", None) or []))
    deleted = sum(len(getattr(h, "deleted_lines", []) or []) for h in (getattr(change, "hunks", None) or []))
    amount = added + deleted
    return amount if amount > 0 else 1  # at least record the touch


def _change_net_churn(change) -> int:
    added = sum(len(getattr(h, "added_lines", []) or []) for h in (getattr(change, "hunks", None) or []))
    deleted = sum(len(getattr(h, "deleted_lines", []) or []) for h in (getattr(change, "hunks", None) or []))
    return added - deleted


def _author_churn(file_) -> dict[str, int]:
    """Lifetime per-author churn for a file. Touch contributes ≥ 1 even if hunks empty."""
    churn: dict[str, int] = {}
    for change in file_.changes or []:
        commit = getattr(change, "commit", None)
        if commit is None:
            continue
        author = getattr(commit, "author", None)
        if author is None:
            continue
        author_id = getattr(author, "id", None) or str(author)
        churn[author_id] = churn.get(author_id, 0) + _change_churn(change)
    return churn


def _author_churn_within(file_, cutoff: Optional[datetime]) -> dict[str, int]:
    """Per-author churn restricted to commits with author_date >= cutoff."""
    if cutoff is None:
        return _author_churn(file_)
    churn: dict[str, int] = {}
    for change in file_.changes or []:
        commit = getattr(change, "commit", None)
        if commit is None:
            continue
        d = ensure_aware(getattr(commit, "author_date", None))
        if d is None or d < cutoff:
            continue
        author = getattr(commit, "author", None)
        if author is None:
            continue
        author_id = getattr(author, "id", None) or str(author)
        churn[author_id] = churn.get(author_id, 0) + _change_churn(change)
    return churn


def _active_author_churn(
    file_,
    tags_by_entity: dict,
    cutoff: Optional[datetime],
) -> dict[str, int]:
    """Recent-window churn limited to authors carrying classifiers.activity=active.

    Used by WeakOwnership: how much of *recent* churn comes from "currently
    active" authors (per the AuthorClassifiersTagger's threshold).
    """
    recent = _author_churn_within(file_, cutoff)
    if not recent:
        return {}
    out: dict[str, int] = {}
    for author_id, churn in recent.items():
        atags = tags_by_entity.get(f"author:{author_id}")
        if atags is None:
            continue
        if atags.classifiers.get("activity") == "active":
            out[author_id] = churn
    return out


def _commit_dates(file_) -> list[datetime]:
    out: list[datetime] = []
    for change in file_.changes or []:
        commit = getattr(change, "commit", None)
        if commit is None:
            continue
        d = ensure_aware(getattr(commit, "author_date", None))
        if d is not None:
            out.append(d)
    return out


def _bucket_start(dt: datetime, anchor: datetime, bucket_weeks: int) -> datetime:
    """Anchor-aligned bucket start for `dt`. Buckets count back from anchor."""
    width = timedelta(weeks=bucket_weeks)
    delta = anchor - dt
    n_buckets = int(delta // width)
    return anchor - (n_buckets + 1) * width


def _time_bucketed_churn(
    file_,
    bucket_weeks: int,
    anchor: Optional[datetime] = None,
) -> list[tuple[datetime, int]]:
    """Ordered (bucket_start, net_churn=added-deleted) pairs across the file's lifetime.

    Buckets are anchored to the file's most recent commit (or `anchor` if
    provided). Empty buckets between commits are NOT inserted — Accumulator
    requires we count "windows where net churn > 0" and absent windows are
    irrelevant; Erosion uses contiguous windows so it inserts fillers itself.
    """
    by_bucket: dict[datetime, int] = {}
    dates = []
    for change in file_.changes or []:
        commit = getattr(change, "commit", None)
        if commit is None:
            continue
        d = ensure_aware(getattr(commit, "author_date", None))
        if d is None:
            continue
        dates.append((d, change))
    if not dates:
        return []
    if anchor is None:
        anchor = max(d for d, _ in dates)
    for d, change in dates:
        bs = _bucket_start(d, anchor, bucket_weeks)
        by_bucket[bs] = by_bucket.get(bs, 0) + _change_net_churn(change)
    return sorted(by_bucket.items(), key=lambda kv: kv[0])


def _time_bucketed_commits(
    file_,
    bucket_weeks: int,
    anchor: Optional[datetime] = None,
    fill_gaps: bool = False,
) -> list[tuple[datetime, int]]:
    """Ordered (bucket_start, commit_count) pairs.

    When `fill_gaps=True`, empty buckets between the earliest and latest
    observed are inserted with count=0 — needed for trend-fitting (Erosion).
    """
    by_bucket: dict[datetime, int] = {}
    dates = _commit_dates(file_)
    if not dates:
        return []
    if anchor is None:
        anchor = max(dates)
    width = timedelta(weeks=bucket_weeks)
    for d in dates:
        bs = _bucket_start(d, anchor, bucket_weeks)
        by_bucket[bs] = by_bucket.get(bs, 0) + 1
    if not fill_gaps:
        return sorted(by_bucket.items(), key=lambda kv: kv[0])

    starts = sorted(by_bucket.keys())
    earliest, latest = starts[0], starts[-1]
    out: list[tuple[datetime, int]] = []
    cur = earliest
    while cur <= latest:
        out.append((cur, by_bucket.get(cur, 0)))
        cur = cur + width
    return out


def _linear_slope(values: list[float]) -> Optional[float]:
    """Least-squares slope of `values` against index 0..n-1. None if n<2."""
    n = len(values)
    if n < 2:
        return None
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = 0.0
    den = 0.0
    for i, y in enumerate(values):
        dx = i - mean_x
        num += dx * (y - mean_y)
        den += dx * dx
    if den == 0:
        return None
    return num / den
