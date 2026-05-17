"""Pace overview — v2 port (Chunk 17).

Port of legacy ``src/enrichment/overview/pace_table.py``. Reports commit
cadence and natural-language commit-mix per top-level folder.

Reads:

* ``graph.commits`` + ``graph.changes`` for the per-folder bucketing
  (a commit "belongs" to every top-folder its changes touched).
* ``graph.classifiers`` (dimension ``message.nature``) for nature
  buckets — emitted by :class:`CommitClassifierMetric`.

Columns (lifetime + recent + trend% where applicable):

  commits_per_week, pct_bugfix, pct_feature, pct_refactor, pct_docs,
  pct_chore, pct_off_hours, pct_weekend, distinct_authors.
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, ClassVar, Optional

from src.common.domains.components.resolver import top_folder_of
from src.common.kernel import EntityKind, EntityRef
from src.enrichment.overviews.models import (
    OverviewCell,
    OverviewRow,
    OverviewTable,
    OverviewTableBuilder,
)
from src.enrichment.overviews.registries import OVERVIEWS
from src.enrichment.recent_window import ensure_aware, trend_percent

if TYPE_CHECKING:
    from src.common.kernel import Graph


COLUMNS: list[str] = [
    "commits_per_week",
    "pct_bugfix",
    "pct_feature",
    "pct_refactor",
    "pct_docs",
    "pct_chore",
    "pct_off_hours",
    "pct_weekend",
    "distinct_authors",
]

_NATURE_LABELS: tuple[str, ...] = (
    "bugfix", "feature", "refactor", "docs", "chore",
)

# Local working-hours convention (Mon-Fri 08:00-17:59).
_WORKING_HOURS = range(8, 18)

_DEFAULT_RECENT_WINDOW_DAYS = 90


@OVERVIEWS.register
class PaceTableBuilder(OverviewTableBuilder):
    """One row per top-level folder + a synthetic ``(project)`` aggregate."""

    name: ClassVar[str] = "pace"

    def build(self, graph: "Graph", config: Any) -> OverviewTable:
        commits_reg = getattr(graph, "commits", None)
        if commits_reg is None:
            return OverviewTable(
                name=self.name, entity_kind="component",
                columns=COLUMNS, rows=[],
            )
        try:
            commits = list(commits_reg)
        except TypeError:
            commits = []

        cutoff = _resolve_recent_cutoff(graph)
        recent_window_days = _config_field(
            config, "recent_window_days", _DEFAULT_RECENT_WINDOW_DAYS,
        )
        nature_by_commit_id = _nature_by_commit_id(graph)
        commits_by_folder = _bucket_commits_by_top_folder(graph, commits)

        rows: list[OverviewRow] = [
            _row_for(
                "(project)", commits, cutoff,
                recent_window_days, nature_by_commit_id,
            )
        ]
        for folder, folder_commits in sorted(commits_by_folder.items()):
            rows.append(
                _row_for(
                    folder, folder_commits, cutoff,
                    recent_window_days, nature_by_commit_id,
                )
            )

        return OverviewTable(
            name=self.name,
            entity_kind="component",
            columns=COLUMNS,
            rows=rows,
        )


# ----------------------------------------------------------------------
# Per-row aggregation
# ----------------------------------------------------------------------
def _row_for(
    entity_id: str,
    lifetime_commits: list[Any],
    cutoff: Optional[Any],
    recent_window_days: int,
    nature_by_commit_id: dict[str, str],
) -> OverviewRow:
    if cutoff is None:
        recent_commits = list(lifetime_commits)
    else:
        recent_commits = [
            c for c in lifetime_commits
            if _commit_in_window(c, cutoff)
        ]

    lifetime_days = _commit_span_days(lifetime_commits)

    cells: dict[str, OverviewCell] = {}

    cells["commits_per_week"] = _rate_cell(
        lifetime_commits, recent_commits, lifetime_days, recent_window_days,
    )

    for label in _NATURE_LABELS:
        cells[f"pct_{label}"] = _share_cell(
            lifetime_commits, recent_commits,
            lambda c, n=label: nature_by_commit_id.get(c.id) == n,
        )

    cells["pct_off_hours"] = _share_cell(
        lifetime_commits, recent_commits, _is_off_hours,
    )
    cells["pct_weekend"] = _share_cell(
        lifetime_commits, recent_commits, _is_weekend,
    )

    cells["distinct_authors"] = OverviewCell(
        lifetime_value=_distinct_authors(lifetime_commits),
        recent_value=_distinct_authors(recent_commits),
        trend_percent=None,
    )

    return OverviewRow(entity_id=entity_id, cells=cells)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _bucket_commits_by_top_folder(
    graph: Any, commits: list[Any]
) -> dict[str, list[Any]]:
    """Bucket each commit under every top-folder its changes touch."""
    changes_by_commit = _changes_by_commit_index(graph)
    out: dict[str, list[Any]] = defaultdict(list)
    for commit in commits:
        folders: set[str] = set()
        for ch in changes_by_commit(commit.ref()):
            fref = getattr(ch, "file_ref", None)
            if fref is None:
                continue
            top = top_folder_of(fref.id)
            if top is None:
                continue
            folders.add(top)
        for folder in folders:
            out[folder].append(commit)
    return dict(out)


def _changes_by_commit_index(graph: Any):
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _ref: []
    by_commit = getattr(changes, "by_commit", None)
    if by_commit is not None:
        return lambda commit_ref: by_commit[commit_ref]

    def scan(commit_ref):
        return [ch for ch in changes if getattr(ch, "commit_ref", None) == commit_ref]

    return scan


def _nature_by_commit_id(graph: Any) -> dict[str, str]:
    """Index of ``commit_id -> nature`` from the classifier registry."""
    classifiers = getattr(graph, "classifiers", None)
    if classifiers is None:
        return {}
    of_dimension = getattr(classifiers, "of_dimension", None)
    if of_dimension is None:
        return {}
    out: dict[str, str] = {}
    for cls_obj in of_dimension("message.nature"):
        target: EntityRef = cls_obj.target
        if target.kind != EntityKind.COMMIT:
            continue
        out[target.id] = cls_obj.value
    return out


def _commit_span_days(commits: list[Any]) -> Optional[int]:
    dates = [
        ensure_aware(
            getattr(c, "author_date", None) or getattr(c, "committer_date", None)
        )
        for c in commits
    ]
    dates = [d for d in dates if d is not None]
    if len(dates) < 2:
        return None
    return max(1, (max(dates) - min(dates)).days)


def _commit_in_window(commit: Any, cutoff: Any) -> bool:
    d = ensure_aware(
        getattr(commit, "author_date", None)
        or getattr(commit, "committer_date", None)
    )
    if d is None or cutoff is None:
        return False
    try:
        return d >= cutoff
    except TypeError:
        return False


def _is_off_hours(commit: Any) -> bool:
    dt = ensure_aware(getattr(commit, "author_date", None))
    if dt is None:
        return False
    return dt.hour not in _WORKING_HOURS


def _is_weekend(commit: Any) -> bool:
    dt = ensure_aware(getattr(commit, "author_date", None))
    if dt is None:
        return False
    # Python: Monday=0, Sunday=6
    return dt.weekday() >= 5


def _distinct_authors(commits: list[Any]) -> int:
    ids: set[str] = set()
    for c in commits:
        a_ref = getattr(c, "author_ref", None)
        if a_ref is None:
            continue
        ids.add(a_ref.id)
    return len(ids)


def _rate_cell(
    lifetime_items: list[Any],
    recent_items: list[Any],
    lifetime_days: Optional[int],
    recent_days: Optional[int],
) -> OverviewCell:
    lt = _rate_per_week(lifetime_items, lifetime_days)
    rc = _rate_per_week(recent_items, recent_days)
    return OverviewCell(
        lifetime_value=lt,
        recent_value=rc,
        trend_percent=trend_percent(lt, rc),
    )


def _rate_per_week(items: list[Any], window_days: Optional[int]) -> Optional[float]:
    if window_days is None or window_days <= 0:
        return None
    count = len(items)
    weeks = window_days / 7.0
    if weeks == 0:
        return None
    return round(count / weeks, 3)


def _share_cell(
    lifetime_items: list[Any],
    recent_items: list[Any],
    predicate,
) -> OverviewCell:
    def _share(items: list[Any]) -> Optional[float]:
        if not items:
            return None
        matched = sum(1 for i in items if predicate(i))
        return round(100.0 * matched / len(items), 2)

    lt = _share(lifetime_items)
    rc = _share(recent_items)
    return OverviewCell(
        lifetime_value=lt,
        recent_value=rc,
        trend_percent=trend_percent(lt, rc),
    )


def _config_field(config: Any, name: str, default: Any) -> Any:
    if config is None:
        return default
    value = getattr(config, name, None)
    if value is None:
        return default
    return value


def _resolve_recent_cutoff(graph: Any) -> Optional[Any]:
    explicit = getattr(graph, "recent_cutoff", None)
    if explicit is not None:
        return ensure_aware(explicit)
    return None


__all__ = ["PaceTableBuilder", "COLUMNS"]
