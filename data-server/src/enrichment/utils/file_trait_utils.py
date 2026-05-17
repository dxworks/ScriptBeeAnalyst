"""Shared per-file helpers used by the A2.1 file-level anomaly metrics
and the file-domain cochange relation builders.

Port of legacy ``src/enrichment/tagger/file_trait_utils.py``. The legacy
walked Python back-references on ``File`` / ``Change`` / ``Commit``
objects (``file_.changes``, ``change.commit``, ``commit.author``); v2
has no such back-pointers — every cross-entity link is an
:class:`EntityRef` resolved through the typed registries on
:class:`Graph`. Each helper here takes the ``Graph`` plus the
:class:`EntityRef` it operates on and walks the indexes
(``graph.changes.by_file``, ``graph.hunks.by_change``) to reconstruct
the legacy view.

Where the legacy depended on the ``tags_by_entity`` dict to read
classifier values (e.g. WeakOwnership's "is this author 'active'?"
check), the v2 surface consumes
:meth:`ClassifierRegistry.for_target` / :meth:`ClassifierRegistry.with_value`
directly off ``graph.classifiers``.

Audit (Chunk 11 plan §"Files to CREATE"): only helpers used by the 11
downstream consumers below are kept. If a future port needs an extra
helper, restore it here, do NOT inline a parallel copy in the consumer.

Consumers (planned for Chunks 12 / 15 / 16 / 13 / 14):
  * anomaly_complexity         — change_churn, time_bucketed_churn
  * anomaly_coupling           — change_churn, author_churn
  * anomaly_quality_issues     — change_churn
  * anomaly_testing            — commit_dates, time_bucketed_commits
  * anomaly_structuring        — files_touched_by_account
  * anomaly_cohesion           — commit_dates, time_bucketed_commits,
                                 linear_slope, bucket_start
  * anomaly_knowledge          — author_churn, author_churn_within,
                                 active_author_churn, time_bucketed_churn,
                                 files_touched_by_account
  * cochange_file_*            — change_net_churn (for "skip noisy renames")
  * cochange_component_*       — change_churn
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.recent_window import ensure_aware

if TYPE_CHECKING:
    from src.common.kernel import Graph
    from src.common.domains.git.models import Change, Commit


# ----------------------------------------------------------------------
# Internal index accessors
# ----------------------------------------------------------------------
def _changes_for_file(graph: "Graph", file_ref: EntityRef) -> tuple:
    """Every :class:`Change` whose ``file_ref`` matches ``file_ref``.

    Uses the declared ``by_file`` index on the ChangeRegistry when
    available (the v2 wiring) and falls back to a linear scan otherwise
    so tests using ad-hoc registries still work.
    """
    changes = getattr(graph, "changes", None)
    if changes is None:
        return ()
    by_file = getattr(changes, "by_file", None)
    if by_file is not None:
        return tuple(by_file[file_ref])
    return tuple(ch for ch in changes if getattr(ch, "file_ref", None) == file_ref)


def _hunks_for_change(graph: "Graph", change_ref: EntityRef) -> tuple:
    """Every :class:`Hunk` belonging to ``change_ref``."""
    hunks = getattr(graph, "hunks", None)
    if hunks is None:
        return ()
    by_change = getattr(hunks, "by_change", None)
    if by_change is not None:
        return tuple(by_change[change_ref])
    return tuple(h for h in hunks if getattr(h, "change_ref", None) == change_ref)


def _resolve_commit(graph: "Graph", commit_ref: EntityRef) -> Optional["Commit"]:
    commits = getattr(graph, "commits", None)
    if commits is None:
        return None
    return commits.get(commit_ref.id)


# ----------------------------------------------------------------------
# Churn primitives
# ----------------------------------------------------------------------
def change_churn(graph: "Graph", change: "Change") -> int:
    """Total added+deleted lines for a change; at least 1 if there were
    any hunks (mirrors legacy "record the touch")."""
    added = 0
    deleted = 0
    for hunk in _hunks_for_change(graph, change.ref()):
        added += len(getattr(hunk, "added_lines", []) or [])
        deleted += len(getattr(hunk, "deleted_lines", []) or [])
    amount = added + deleted
    return amount if amount > 0 else 1


def change_net_churn(graph: "Graph", change: "Change") -> int:
    """Net churn (added - deleted) across all hunks of ``change``."""
    added = 0
    deleted = 0
    for hunk in _hunks_for_change(graph, change.ref()):
        added += len(getattr(hunk, "added_lines", []) or [])
        deleted += len(getattr(hunk, "deleted_lines", []) or [])
    return added - deleted


# ----------------------------------------------------------------------
# Author-keyed churn
# ----------------------------------------------------------------------
def author_churn(graph: "Graph", file_ref: EntityRef) -> dict[str, int]:
    """Lifetime per-author churn for the file, keyed by ``author_ref.id``.

    Touch contributes ≥ 1 even if a change had no hunks (mirrors legacy
    "at least record the touch" rule).
    """
    churn: dict[str, int] = {}
    for change in _changes_for_file(graph, file_ref):
        commit = _resolve_commit(graph, change.commit_ref)
        if commit is None:
            continue
        author_ref = getattr(commit, "author_ref", None)
        if author_ref is None:
            continue
        churn[author_ref.id] = churn.get(author_ref.id, 0) + change_churn(graph, change)
    return churn


def author_churn_within(
    graph: "Graph",
    file_ref: EntityRef,
    cutoff: Optional[datetime],
) -> dict[str, int]:
    """Per-author churn restricted to commits whose ``author_date >= cutoff``.

    When ``cutoff`` is ``None`` this collapses to :func:`author_churn`.
    """
    if cutoff is None:
        return author_churn(graph, file_ref)
    cutoff_aware = ensure_aware(cutoff)
    churn: dict[str, int] = {}
    for change in _changes_for_file(graph, file_ref):
        commit = _resolve_commit(graph, change.commit_ref)
        if commit is None:
            continue
        d = ensure_aware(getattr(commit, "author_date", None))
        if d is None or d < cutoff_aware:
            continue
        author_ref = getattr(commit, "author_ref", None)
        if author_ref is None:
            continue
        churn[author_ref.id] = churn.get(author_ref.id, 0) + change_churn(graph, change)
    return churn


def active_author_churn(
    graph: "Graph",
    file_ref: EntityRef,
    cutoff: Optional[datetime],
) -> dict[str, int]:
    """Recent-window churn limited to authors that carry
    ``classifier(dimension='activity', value='active')``.

    Reads from ``graph.classifiers`` (D1/D3-compliant) — no legacy
    ``tags_by_entity`` lookup. The author-activity classifier is emitted
    by the ``author_classifiers`` metric (Chunk 12).
    """
    recent = author_churn_within(graph, file_ref, cutoff)
    if not recent:
        return {}
    classifiers = getattr(graph, "classifiers", None)
    if classifiers is None:
        return {}
    out: dict[str, int] = {}
    for author_id, amount in recent.items():
        author_ref = EntityRef(kind=EntityKind.GIT_ACCOUNT, id=author_id)
        on_author = classifiers.for_target(author_ref) if hasattr(classifiers, "for_target") else {}
        activity = on_author.get("activity") if on_author else None
        if activity is not None and activity.value == "active":
            out[author_id] = amount
    return out


# ----------------------------------------------------------------------
# Per-author file-set lookup (used by OrphanCausers)
# ----------------------------------------------------------------------
def files_touched_by_account(
    graph: "Graph",
    account_ref: EntityRef,
) -> set[EntityRef]:
    """Distinct file refs the account ever authored a change on.

    Walks ``commits.by_author`` + ``changes.by_commit`` so we don't scan
    every commit. Falls back to a full scan if indexes aren't declared.
    """
    files: set[EntityRef] = set()
    commits_reg = getattr(graph, "commits", None)
    if commits_reg is None:
        return files
    by_author = getattr(commits_reg, "by_author", None)
    if by_author is not None:
        author_commits = list(by_author[account_ref])
    else:
        author_commits = [
            c for c in commits_reg if getattr(c, "author_ref", None) == account_ref
        ]
    changes_reg = getattr(graph, "changes", None)
    if changes_reg is None:
        return files
    by_commit = getattr(changes_reg, "by_commit", None)
    for commit in author_commits:
        if by_commit is not None:
            commit_changes = by_commit[commit.ref()]
        else:
            commit_changes = [
                ch for ch in changes_reg if getattr(ch, "commit_ref", None) == commit.ref()
            ]
        for change in commit_changes:
            fref = getattr(change, "file_ref", None)
            if fref is not None:
                files.add(fref)
    return files


# ----------------------------------------------------------------------
# Date / bucket helpers
# ----------------------------------------------------------------------
def commit_dates(graph: "Graph", file_ref: EntityRef) -> list[datetime]:
    """Every commit ``author_date`` for the file, tz-aware (UTC fallback)."""
    out: list[datetime] = []
    for change in _changes_for_file(graph, file_ref):
        commit = _resolve_commit(graph, change.commit_ref)
        if commit is None:
            continue
        d = ensure_aware(getattr(commit, "author_date", None))
        if d is not None:
            out.append(d)
    return out


def bucket_start(dt: datetime, anchor: datetime, bucket_weeks: int) -> datetime:
    """Anchor-aligned bucket start for ``dt``. Buckets count back from
    ``anchor``; the result is always ``<= dt`` and an integer number of
    bucket widths before ``anchor``.
    """
    width = timedelta(weeks=bucket_weeks)
    delta = anchor - dt
    n_buckets = int(delta // width)
    return anchor - (n_buckets + 1) * width


def time_bucketed_churn(
    graph: "Graph",
    file_ref: EntityRef,
    bucket_weeks: int,
    anchor: Optional[datetime] = None,
) -> list[tuple[datetime, int]]:
    """Ordered ``(bucket_start, net_churn)`` pairs across the file's
    lifetime.

    Buckets are anchored to the file's most-recent commit (or the
    supplied ``anchor``). Empty buckets are NOT inserted — Accumulator
    counts "windows where net churn > 0" so the absent windows are
    irrelevant; Erosion uses :func:`time_bucketed_commits` with
    ``fill_gaps=True`` instead.
    """
    by_bucket: dict[datetime, int] = {}
    dated: list[tuple[datetime, "Change"]] = []
    for change in _changes_for_file(graph, file_ref):
        commit = _resolve_commit(graph, change.commit_ref)
        if commit is None:
            continue
        d = ensure_aware(getattr(commit, "author_date", None))
        if d is None:
            continue
        dated.append((d, change))
    if not dated:
        return []
    if anchor is None:
        anchor = max(d for d, _ in dated)
    else:
        anchor = ensure_aware(anchor)
    for d, change in dated:
        bs = bucket_start(d, anchor, bucket_weeks)
        by_bucket[bs] = by_bucket.get(bs, 0) + change_net_churn(graph, change)
    return sorted(by_bucket.items(), key=lambda kv: kv[0])


def time_bucketed_commits(
    graph: "Graph",
    file_ref: EntityRef,
    bucket_weeks: int,
    anchor: Optional[datetime] = None,
    fill_gaps: bool = False,
) -> list[tuple[datetime, int]]:
    """Ordered ``(bucket_start, commit_count)`` pairs.

    Set ``fill_gaps=True`` to insert empty buckets between earliest and
    latest observed — required for trend-fitting (Erosion).
    """
    by_bucket: dict[datetime, int] = {}
    dates = commit_dates(graph, file_ref)
    if not dates:
        return []
    if anchor is None:
        anchor = max(dates)
    else:
        anchor = ensure_aware(anchor)
    width = timedelta(weeks=bucket_weeks)
    for d in dates:
        bs = bucket_start(d, anchor, bucket_weeks)
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


# ----------------------------------------------------------------------
# Pure math (no graph access)
# ----------------------------------------------------------------------
def linear_slope(values: list[float]) -> Optional[float]:
    """Least-squares slope of ``values`` against index ``0..n-1``.

    Returns ``None`` when ``n < 2`` or the x-variance is zero.
    """
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


__all__ = [
    "active_author_churn",
    "author_churn",
    "author_churn_within",
    "bucket_start",
    "change_churn",
    "change_net_churn",
    "commit_dates",
    "files_touched_by_account",
    "linear_slope",
    "time_bucketed_churn",
    "time_bucketed_commits",
]
