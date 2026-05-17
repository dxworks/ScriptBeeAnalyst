"""Authorship overview — v2 port (Chunk 17).

Port of legacy ``src/enrichment/overview/authorship_table.py``. Reads:

* ``graph.files`` + ``graph.changes`` + ``graph.hunks`` for per-author
  churn (mirrors :class:`OwnershipBuilder` — same ``churn = added +
  deleted`` formula, fallback to 1 for binary changes).
* ``graph.classifiers`` (dimension ``seniority``, emitted by
  :class:`AuthorClassifierMetric`) for newcomer / senior shares.
* ``graph.traits`` (name ``anomaly.knowledge.BusFactor1``, emitted by
  :class:`KnowledgeAnomalyMetric`) for bus-factor counts.

Columns (lifetime + recent + trend% where applicable):

  total_authors, active_authors, newcomer_ratio, senior_ratio,
  bus_factor_1_files, dominant_author_share.

Rows: synthetic ``(project)`` aggregate + one per top-level folder.
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
    "total_authors",
    "active_authors",
    "newcomer_ratio",
    "senior_ratio",
    "bus_factor_1_files",
    "dominant_author_share",
]

_BUS_FACTOR_TRAIT = "anomaly.knowledge.BusFactor1"
_SENIORITY_DIM = "seniority"
_NEWCOMER_VALUES: set[str] = {"newcomer"}
_SENIOR_VALUES: set[str] = {"senior", "veteran"}


@OVERVIEWS.register
class AuthorshipTableBuilder(OverviewTableBuilder):
    """One row per top-level folder + a synthetic ``(project)`` aggregate."""

    name: ClassVar[str] = "authorship"

    def build(self, graph: "Graph", config: Any) -> OverviewTable:
        files_reg = getattr(graph, "files", None)
        if files_reg is None:
            return OverviewTable(
                name=self.name, entity_kind="component",
                columns=COLUMNS, rows=[],
            )
        try:
            files = list(files_reg)
        except TypeError:
            files = []

        cutoff = _resolve_recent_cutoff(graph)
        seniority_by_account = _seniority_by_account_id(graph)
        bus_factor_file_ids = _bus_factor_file_ids(graph)

        changes_by_file = _changes_by_file_index(graph)
        commits_get = _entity_by_id(getattr(graph, "commits", None))
        hunks_by_change = _hunks_by_change_index(graph)

        files_by_folder = _files_by_top_folder(files)

        rows: list[OverviewRow] = [
            _row_for(
                "(project)", files, cutoff,
                seniority_by_account, bus_factor_file_ids,
                changes_by_file, commits_get, hunks_by_change,
            )
        ]
        for folder, folder_files in sorted(files_by_folder.items()):
            rows.append(
                _row_for(
                    folder, folder_files, cutoff,
                    seniority_by_account, bus_factor_file_ids,
                    changes_by_file, commits_get, hunks_by_change,
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
    files: list[Any],
    cutoff: Optional[Any],
    seniority_by_account: dict[str, str],
    bus_factor_file_ids: set[str],
    changes_by_file,
    commits_get,
    hunks_by_change,
) -> OverviewRow:
    lifetime_authors: dict[str, int] = defaultdict(int)
    recent_authors: dict[str, int] = defaultdict(int)
    bus_factor_files = 0

    for f in files:
        if f.id in bus_factor_file_ids:
            bus_factor_files += 1
        for ch in changes_by_file(f.ref()):
            commit = commits_get(ch.commit_ref.id)
            if commit is None:
                continue
            author_ref = getattr(commit, "author_ref", None)
            if author_ref is None:
                continue
            aid = author_ref.id
            churn = _change_churn(ch, hunks_by_change)
            lifetime_authors[aid] += churn
            if cutoff is not None and _commit_in_window(commit, cutoff):
                recent_authors[aid] += churn

    cells: dict[str, OverviewCell] = {}

    lt_total = len(lifetime_authors)
    rc_total = len(recent_authors)
    cells["total_authors"] = OverviewCell(
        lifetime_value=lt_total,
        recent_value=rc_total,
        trend_percent=trend_percent(lt_total or None, rc_total or None),
    )
    cells["active_authors"] = OverviewCell(
        lifetime_value=lt_total,
        recent_value=rc_total,
        trend_percent=None,
    )

    cells["newcomer_ratio"] = OverviewCell(
        lifetime_value=_seniority_share(
            lifetime_authors.keys(), seniority_by_account, _NEWCOMER_VALUES,
        ),
        recent_value=_seniority_share(
            recent_authors.keys(), seniority_by_account, _NEWCOMER_VALUES,
        ),
        trend_percent=None,
    )
    cells["senior_ratio"] = OverviewCell(
        lifetime_value=_seniority_share(
            lifetime_authors.keys(), seniority_by_account, _SENIOR_VALUES,
        ),
        recent_value=_seniority_share(
            recent_authors.keys(), seniority_by_account, _SENIOR_VALUES,
        ),
        trend_percent=None,
    )

    cells["bus_factor_1_files"] = OverviewCell(
        lifetime_value=bus_factor_files,
        recent_value=bus_factor_files,
        trend_percent=None,
    )

    cells["dominant_author_share"] = OverviewCell(
        lifetime_value=_dominant_share(lifetime_authors),
        recent_value=_dominant_share(recent_authors),
        trend_percent=None,
    )

    return OverviewRow(entity_id=entity_id, cells=cells)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _files_by_top_folder(files: list[Any]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = defaultdict(list)
    for f in files:
        top = top_folder_of(f.id)
        if top is None:
            continue
        out[top].append(f)
    return dict(out)


def _seniority_by_account_id(graph: Any) -> dict[str, str]:
    """Index of ``account_id -> seniority value`` from the classifier registry."""
    classifiers = getattr(graph, "classifiers", None)
    if classifiers is None:
        return {}
    of_dimension = getattr(classifiers, "of_dimension", None)
    if of_dimension is None:
        return {}
    out: dict[str, str] = {}
    for cls_obj in of_dimension(_SENIORITY_DIM):
        target: EntityRef = cls_obj.target
        if target.kind != EntityKind.GIT_ACCOUNT:
            continue
        out[target.id] = cls_obj.value
    return out


def _bus_factor_file_ids(graph: Any) -> set[str]:
    """Set of file ids carrying the ``BusFactor1`` knowledge trait."""
    traits = getattr(graph, "traits", None)
    if traits is None:
        return set()
    of_name = getattr(traits, "of_name", None)
    if of_name is None:
        return set()
    out: set[str] = set()
    for t in of_name(_BUS_FACTOR_TRAIT):
        target: EntityRef = t.target
        if target.kind != EntityKind.FILE:
            continue
        out.add(target.id)
    return out


def _seniority_share(
    author_ids,
    seniority_by_account: dict[str, str],
    target_values: set[str],
) -> Optional[float]:
    ids = list(author_ids)
    if not ids:
        return None
    matched = 0
    for aid in ids:
        value = seniority_by_account.get(aid)
        if value in target_values:
            matched += 1
    return round(matched / len(ids), 4)


def _dominant_share(churn_by_author: dict[str, int]) -> Optional[float]:
    if not churn_by_author:
        return None
    total = sum(churn_by_author.values())
    if total <= 0:
        return None
    top = max(churn_by_author.values())
    return round(top / total, 4)


def _changes_by_file_index(graph: Any):
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _file_ref: []
    by_file = getattr(changes, "by_file", None)
    if by_file is not None:
        return lambda file_ref: by_file[file_ref]

    def scan(file_ref):
        return [ch for ch in changes if ch.file_ref == file_ref]

    return scan


def _entity_by_id(reg: Any):
    if reg is None:
        return lambda _id: None
    get = getattr(reg, "get", None)
    if get is None:
        return lambda _id: None
    return get


def _hunks_by_change_index(graph: Any):
    hunks = getattr(graph, "hunks", None)
    if hunks is None:
        return lambda _change_ref: []
    by_change = getattr(hunks, "by_change", None)
    if by_change is not None:
        return lambda change_ref: by_change[change_ref]

    def scan(change_ref):
        return [h for h in hunks if h.change_ref == change_ref]

    return scan


def _change_churn(change: Any, hunks_by_change) -> int:
    """Sum of added+deleted lines across all hunks of a change.

    Mirrors :class:`OwnershipBuilder._change_churn`: returns at least 1
    so a binary / no-hunk change still records the touch.
    """
    total = 0
    for hunk in hunks_by_change(change.ref()):
        total += len(getattr(hunk, "added_lines", []) or [])
        total += len(getattr(hunk, "deleted_lines", []) or [])
    return total if total > 0 else 1


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


def _resolve_recent_cutoff(graph: Any) -> Optional[Any]:
    explicit = getattr(graph, "recent_cutoff", None)
    if explicit is not None:
        return ensure_aware(explicit)
    return None


__all__ = ["AuthorshipTableBuilder", "COLUMNS"]
