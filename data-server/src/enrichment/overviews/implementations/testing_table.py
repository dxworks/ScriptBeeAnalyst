"""Testing overview — v2 port (Chunk 18).

Port of legacy ``src/enrichment/overview/testing_table.py``. Rolls
test-coverage signals per top-level folder.

Reads:

* ``graph.files``                                — file population.
* ``graph.commits`` + ``graph.changes``          — commit/folder
  bucketing (a commit "belongs" to every top-folder its changes touch).
* ``graph.classifiers`` (dimension ``role``)     — Chunk-12
  :class:`FileClassifierMetric` emits one per file; we filter on the
  ``"test"`` / ``"production"`` values.
* ``graph.classifiers`` (dimension ``message.nature``) — bugfix-share
  rollup over the commits in scope.
* ``graph.traits`` (name ``anomaly.testing.BugMagnet``) — Chunk-15
  :class:`AnomalyTestingMetric` emits per-file; we count per folder.

Columns:

  test_file_ratio, bugfix_commit_ratio, bugmagnet_files,
  test_to_prod_ratio.

Rows: synthetic ``(project)`` aggregate + one per top-level folder
(union of file-folder and commit-folder rollups, mirroring legacy).
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
    "test_file_ratio",
    "bugfix_commit_ratio",
    "bugmagnet_files",
    "test_to_prod_ratio",
]

_ROLE_DIM = "role"
_NATURE_DIM = "message.nature"
_BUGMAGNET_TRAIT = "anomaly.testing.BugMagnet"


@OVERVIEWS.register
class TestingTableBuilder(OverviewTableBuilder):
    """One row per top-level folder + a synthetic ``(project)`` aggregate."""

    name: ClassVar[str] = "testing"

    def build(self, graph: "Graph", config: Any) -> OverviewTable:
        files_reg = getattr(graph, "files", None)
        commits_reg = getattr(graph, "commits", None)
        if files_reg is None and commits_reg is None:
            return OverviewTable(
                name=self.name, entity_kind="component",
                columns=COLUMNS, rows=[],
            )

        files = _safe_list(files_reg)
        commits = _safe_list(commits_reg)

        cutoff = _resolve_recent_cutoff(graph)
        role_by_file_id = _classifier_by_target_id(graph, _ROLE_DIM, EntityKind.FILE)
        nature_by_commit_id = _classifier_by_target_id(graph, _NATURE_DIM, EntityKind.COMMIT)
        bugmagnet_file_ids = _file_ids_with_trait(graph, _BUGMAGNET_TRAIT)

        files_by_folder = _files_by_top_folder(files)
        commits_by_folder = _commits_by_top_folder(graph, commits)

        rows: list[OverviewRow] = [
            _row_for(
                "(project)", files, commits, cutoff,
                role_by_file_id, nature_by_commit_id,
                bugmagnet_file_ids,
            )
        ]
        for folder in sorted(set(files_by_folder.keys()) | set(commits_by_folder.keys())):
            rows.append(
                _row_for(
                    folder,
                    files_by_folder.get(folder, []),
                    commits_by_folder.get(folder, []),
                    cutoff,
                    role_by_file_id, nature_by_commit_id,
                    bugmagnet_file_ids,
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
    commits: list[Any],
    cutoff: Optional[Any],
    role_by_file_id: dict[str, str],
    nature_by_commit_id: dict[str, str],
    bugmagnet_file_ids: set[str],
) -> OverviewRow:
    test_count = 0
    prod_count = 0
    bugmagnet_count = 0
    for f in files:
        if f.id in bugmagnet_file_ids:
            bugmagnet_count += 1
        role = role_by_file_id.get(f.id)
        if role == "test":
            test_count += 1
        elif role == "production":
            prod_count += 1

    total_files = len(files)

    recent_commits = (
        [c for c in commits if _commit_in_window(c, cutoff)]
        if cutoff is not None else list(commits)
    )

    cells: dict[str, OverviewCell] = {}
    cells["test_file_ratio"] = OverviewCell(
        lifetime_value=_ratio(test_count, total_files),
        recent_value=_ratio(test_count, total_files),
        trend_percent=None,
    )

    lt_bugfix = _bugfix_ratio(commits, nature_by_commit_id)
    rc_bugfix = _bugfix_ratio(recent_commits, nature_by_commit_id)
    cells["bugfix_commit_ratio"] = OverviewCell(
        lifetime_value=lt_bugfix,
        recent_value=rc_bugfix,
        trend_percent=trend_percent(lt_bugfix, rc_bugfix),
    )

    cells["bugmagnet_files"] = OverviewCell(
        lifetime_value=bugmagnet_count,
        recent_value=bugmagnet_count,
        trend_percent=None,
    )
    cells["test_to_prod_ratio"] = OverviewCell(
        lifetime_value=_ratio(test_count, prod_count),
        recent_value=_ratio(test_count, prod_count),
        trend_percent=None,
    )

    return OverviewRow(entity_id=entity_id, cells=cells)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_list(reg: Any) -> list[Any]:
    if reg is None:
        return []
    try:
        return list(reg)
    except TypeError:
        return []


def _files_by_top_folder(files: list[Any]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = defaultdict(list)
    for f in files:
        top = top_folder_of(f.id)
        if top is None:
            continue
        out[top].append(f)
    return dict(out)


def _commits_by_top_folder(graph: Any, commits: list[Any]) -> dict[str, list[Any]]:
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
        return lambda _commit_ref: []
    by_commit = getattr(changes, "by_commit", None)
    if by_commit is not None:
        return lambda commit_ref: by_commit[commit_ref]

    def scan(commit_ref):
        return [ch for ch in changes if getattr(ch, "commit_ref", None) == commit_ref]

    return scan


def _classifier_by_target_id(graph: Any, dimension: str, kind: EntityKind) -> dict[str, str]:
    classifiers = getattr(graph, "classifiers", None)
    if classifiers is None:
        return {}
    of_dimension = getattr(classifiers, "of_dimension", None)
    if of_dimension is None:
        return {}
    out: dict[str, str] = {}
    for cls_obj in of_dimension(dimension):
        target: EntityRef = cls_obj.target
        if target.kind != kind:
            continue
        out[target.id] = cls_obj.value
    return out


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


def _ratio(n: int, d: int) -> Optional[float]:
    if d <= 0:
        return None
    return round(n / d, 4)


def _bugfix_ratio(
    commits: list[Any], nature_by_commit_id: dict[str, str],
) -> Optional[float]:
    if not commits:
        return None
    bug = sum(
        1 for c in commits if nature_by_commit_id.get(c.id) == "bugfix"
    )
    return round(bug / len(commits), 4)


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


__all__ = ["TestingTableBuilder", "COLUMNS"]
