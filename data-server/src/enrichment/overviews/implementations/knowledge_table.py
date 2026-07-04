"""Knowledge overview — v2 port (Chunk 17).

Port of legacy ``src/enrichment/overview/knowledge_table.py``. Aggregates
knowledge-anomaly trait counts + ownership ratios per top-level folder.

Reads:

* ``graph.files`` + ``graph.changes`` + ``graph.hunks`` for per-author
  churn (same churn formula as :class:`OwnershipBuilder`).
* ``graph.classifiers`` (dimensions ``activity`` + ``seniority``) for
  the APK% and newcomer ratio columns.
* ``graph.traits`` (names ``anomaly.knowledge.WeakOwnership`` /
  ``anomaly.knowledge.PolarisedOwnership`` /
  ``anomaly.knowledge.Orphan``) for the per-folder count columns.

Columns (lifetime + recent + trend% on share-like cells):

  apk_percent, weak_ownership_count, polarised_ownership_count,
  orphan_count, newcomer_ratio.

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
    "apk_percent",
    "weak_ownership_count",
    "polarised_ownership_count",
    "orphan_count",
    "newcomer_ratio",
]


# Ownership-anomaly trait names — counted as raw lifetime totals (no
# recent delta because the underlying metric fires once per file, not
# per-window).
_OWNERSHIP_TRAITS: dict[str, str] = {
    "weak_ownership_count":      "anomaly.knowledge.WeakOwnership",
    "polarised_ownership_count": "anomaly.knowledge.PolarisedOwnership",
    "orphan_count":              "anomaly.knowledge.Orphan",
}

_ACTIVITY_DIM = "activity"
_SENIORITY_DIM = "seniority"
_NEWCOMER_VALUES: set[str] = {"newcomer"}


@OVERVIEWS.register
class KnowledgeTableBuilder(OverviewTableBuilder):
    """One row per top-level folder + a synthetic ``(project)`` aggregate."""

    name: ClassVar[str] = "knowledge"

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
        activity_by_account = _classifier_by_account_id(graph, _ACTIVITY_DIM)
        seniority_by_account = _classifier_by_account_id(graph, _SENIORITY_DIM)
        trait_file_ids = {
            col: _file_ids_with_trait(graph, trait_name)
            for col, trait_name in _OWNERSHIP_TRAITS.items()
        }

        changes_by_file = _changes_by_file_index(graph)
        commits_get = _entity_by_id(getattr(graph, "commits", None))
        hunks_by_change = _hunks_by_change_index(graph)

        files_by_folder = _files_by_top_folder(files)

        rows: list[OverviewRow] = [
            _row_for(
                "(project)", files, cutoff,
                activity_by_account, seniority_by_account, trait_file_ids,
                changes_by_file, commits_get, hunks_by_change,
            )
        ]
        for folder, folder_files in sorted(files_by_folder.items()):
            rows.append(
                _row_for(
                    folder, folder_files, cutoff,
                    activity_by_account, seniority_by_account, trait_file_ids,
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
    activity_by_account: dict[str, str],
    seniority_by_account: dict[str, str],
    trait_file_ids: dict[str, set[str]],
    changes_by_file,
    commits_get,
    hunks_by_change,
) -> OverviewRow:
    lifetime_churn: dict[str, int] = defaultdict(int)
    recent_churn: dict[str, int] = defaultdict(int)
    trait_counts: dict[str, int] = {k: 0 for k in _OWNERSHIP_TRAITS}

    for f in files:
        for col, file_ids in trait_file_ids.items():
            if f.id in file_ids:
                trait_counts[col] += 1
        for ch in changes_by_file(f.ref()):
            commit = commits_get(ch.commit_ref.id)
            if commit is None:
                continue
            author_ref = getattr(commit, "author_ref", None)
            if author_ref is None:
                continue
            aid = author_ref.id
            churn = _change_churn(ch, hunks_by_change)
            lifetime_churn[aid] += churn
            if cutoff is not None and _commit_in_window(commit, cutoff):
                recent_churn[aid] += churn

    cells: dict[str, OverviewCell] = {}

    # APK% — share (in %) of churn coming from currently-active authors.
    lt_apk = _apk_percent(lifetime_churn, activity_by_account)
    rc_apk = _apk_percent(recent_churn, activity_by_account)
    cells["apk_percent"] = OverviewCell(
        lifetime_value=lt_apk,
        recent_value=rc_apk,
        trend_percent=trend_percent(lt_apk, rc_apk),
    )

    # Raw lifetime counts — no per-window split (mirrors how
    # bus_factor_1_files is exposed in the Authorship table).
    for col, count in trait_counts.items():
        cells[col] = OverviewCell(
            lifetime_value=count,
            recent_value=None,
            trend_percent=None,
        )

    lt_newcomer = _seniority_share(
        lifetime_churn.keys(), seniority_by_account, _NEWCOMER_VALUES
    )
    rc_newcomer = _seniority_share(
        recent_churn.keys(), seniority_by_account, _NEWCOMER_VALUES
    )
    cells["newcomer_ratio"] = OverviewCell(
        lifetime_value=lt_newcomer,
        recent_value=rc_newcomer,
        trend_percent=trend_percent(lt_newcomer, rc_newcomer),
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


def _classifier_by_account_id(graph: Any, dimension: str) -> dict[str, str]:
    classifiers = getattr(graph, "classifiers", None)
    if classifiers is None:
        return {}
    of_dimension = getattr(classifiers, "of_dimension", None)
    if of_dimension is None:
        return {}
    expected_kind = _author_target_kind(graph)
    out: dict[str, str] = {}
    for cls_obj in of_dimension(dimension):
        target: EntityRef = cls_obj.target
        # Post-finalize (UnifiedUsers redesign §H) author-classifier
        # targets are UnifiedUser refs — phase B emits them after
        # ``rebind_account_refs_to_unified`` reroutes ``commit.author_ref``.
        # Pre-finalize the targets are still ``GIT_ACCOUNT``.
        if target.kind != expected_kind:
            continue
        out[target.id] = cls_obj.value
    return out


def _author_target_kind(graph: Any) -> EntityKind:
    """Author-side classifier-target kind for this graph's lifecycle phase.

    Mirrors :func:`anomaly_knowledge._author_target_kind` so the
    state-aware kind check is consistent across the people-side
    enrichment surface.
    """
    try:
        from src.common.kernel.merge_state import MergeState
    except Exception:  # pragma: no cover — defensive
        return EntityKind.UNIFIED_USER
    state = getattr(graph, "merge_state", None)
    if state == MergeState.FINALIZED:
        return EntityKind.UNIFIED_USER
    return EntityKind.GIT_ACCOUNT


def _file_ids_with_trait(graph: Any, trait_name: str) -> set[str]:
    traits = getattr(graph, "traits", None)
    if traits is None:
        return set()
    of_name = getattr(traits, "of_name", None)
    if of_name is None:
        return set()
    out: set[str] = set()
    for t in of_name(trait_name):
        target: EntityRef = t.target
        if target.kind != EntityKind.FILE:
            continue
        out.add(target.id)
    return out


def _apk_percent(
    churn_by_author: dict[str, int],
    activity_by_account: dict[str, str],
) -> Optional[float]:
    total = sum(churn_by_author.values())
    if total <= 0:
        return None
    active = 0
    for aid, churn in churn_by_author.items():
        if activity_by_account.get(aid) == "active":
            active += churn
    return round(100.0 * active / total, 2)


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


__all__ = ["KnowledgeTableBuilder", "COLUMNS"]
