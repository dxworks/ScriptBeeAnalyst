"""Nature distribution overview — v2 port.

Port of legacy ``src/enrichment/overview/nature_table.py``. Standalone
per-folder breakdown of the commit-message-nature mix (the same nature
values the Pace table also rolls up, but presented as a separate table
so trend visualisations can isolate "did bug-fixes spike?" from pace
cadence).

Columns (one ``<nature>_pct`` cell per nature, ``lifetime + recent +
trend%`` triple per cell):

  bugfix, feature, refactor, docs, test, chore, merge, revert.

Rows: synthetic ``(project)`` aggregate + one per top-level folder. A
commit "belongs" to every top-folder it touched (a commit that
modifies files in both ``src/`` and ``tests/`` shows up in BOTH
rows). Mirrors the legacy ``pace_table`` rollup rule.

Nature data source: the ``commit.classifiers`` metric
(``CommitClassifierMetric``) emits one ``Classifier(dimension=
"message.nature", value=<nature>)`` per commit. The legacy used
``tags_by_entity[f"commit:{id}"].classifiers["message.nature"]``; v2
reads the classifier registry via
``graph.classifiers.with_value("message.nature", nature)`` and indexes
back to the commit by target ref. This is the D3-style read that the
plan §5 ("Reuse map") explicitly calls out.
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
from src.enrichment.recent_window import ensure_aware

if TYPE_CHECKING:
    from src.common.kernel import Graph


# Order mirrors the spec; revert kept last because it's rare enough that
# any non-zero cell is worth eyeballing.
_NATURES: tuple[str, ...] = (
    "bugfix",
    "feature",
    "refactor",
    "docs",
    "test",
    "chore",
    "merge",
    "revert",
)
_COLUMNS: list[str] = [f"{n}_pct" for n in _NATURES]


@OVERVIEWS.register
class NatureTableBuilder(OverviewTableBuilder):
    """One row per top-level folder + a synthetic ``(project)`` aggregate."""

    name: ClassVar[str] = "nature"

    def build(self, graph: "Graph", config: Any) -> OverviewTable:
        commits_reg = getattr(graph, "commits", None)
        if commits_reg is None:
            return OverviewTable(
                name=self.name, entity_kind="component",
                columns=_COLUMNS, rows=[],
            )
        try:
            commits = list(commits_reg)
        except TypeError:
            commits = []

        nature_by_commit_id = _nature_by_commit_id(graph)
        commits_by_folder = _bucket_commits_by_top_folder(graph, commits)
        cutoff = _resolve_recent_cutoff(graph)

        rows: list[OverviewRow] = [
            _row_for("(project)", commits, cutoff, nature_by_commit_id),
        ]
        for folder, folder_commits in sorted(commits_by_folder.items()):
            rows.append(
                _row_for(folder, folder_commits, cutoff, nature_by_commit_id)
            )

        return OverviewTable(
            name=self.name,
            entity_kind="component",
            columns=_COLUMNS,
            rows=rows,
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _row_for(
    entity_id: str,
    lifetime_commits: list[Any],
    cutoff: Optional[Any],
    nature_by_commit_id: dict[str, str],
) -> OverviewRow:
    if cutoff is None:
        recent_commits = list(lifetime_commits)
    else:
        recent_commits = [
            c for c in lifetime_commits
            if _commit_in_window(c, cutoff)
        ]
    cells: dict[str, OverviewCell] = {}
    for nature in _NATURES:
        cells[f"{nature}_pct"] = _share_cell(
            lifetime_commits,
            recent_commits,
            lambda c, n=nature: nature_by_commit_id.get(c.id) == n,
        )
    return OverviewRow(entity_id=entity_id, cells=cells)


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


def _resolve_recent_cutoff(graph: Any) -> Optional[Any]:
    """Honour ``graph.recent_cutoff`` when the caller attached one.

    Production builds don't carry this today; legacy tests do, and the
    builder pattern across v2 (`coauthor`, `cochange`, `ownership`)
    reads it the same way.
    """
    explicit = getattr(graph, "recent_cutoff", None)
    if explicit is not None:
        return ensure_aware(explicit)
    return None


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


def _share_cell(
    lifetime_items: list[Any],
    recent_items: list[Any],
    predicate,
) -> OverviewCell:
    """Build a %-share cell with trend%. Mirrors legacy ``share_cell``."""
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
        trend_percent=_trend_percent(lt, rc),
    )


def _trend_percent(lt: Optional[float], rc: Optional[float]) -> Optional[float]:
    if lt is None or rc is None or lt == 0:
        return None
    return round(((rc - lt) / lt) * 100.0, 2)


__all__ = ["NatureTableBuilder"]
