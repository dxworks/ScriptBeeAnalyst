"""Feature-traceability overview — v2 port (Chunk 18).

Port of legacy
``src/enrichment/overview/feature_traceability_table.py``. Answers
"for this scope, what fraction of code activity is anchored to an
issue?".

Reads:

* ``graph.commits`` + ``graph.changes``           — commit population +
  per-folder bucketing.
* ``graph.relations.of_kind("issue_file")``       — issue ↔ file
  edges (Chunk 7 builder); transformed into per-commit issue linkage
  by joining on the linking commit (via ``changes.by_file``).
* ``graph.issues``                                — issue population
  (denominator for ``issues_with_commits_pct``).

Columns (lifetime + recent + trend% where applicable):

  commits_linked_pct, issues_with_commits_pct,
  mean_issues_per_component.

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
    "commits_linked_pct",
    "issues_with_commits_pct",
    "mean_issues_per_component",
]


@OVERVIEWS.register
class FeatureTraceabilityTableBuilder(OverviewTableBuilder):
    """One row per top-level folder + a synthetic ``(project)`` aggregate."""

    name: ClassVar[str] = "feature_traceability"

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

        # Build the canonical "which issues touch this commit?" / "what
        # folders does this commit touch?" indexes once.
        commit_folders = _commit_folder_index(graph, commits)
        commit_issues = _commit_issue_index(graph)

        # Bucket commits per folder (a commit belongs to every folder
        # its changes touch).
        commits_by_folder: dict[str, list[Any]] = defaultdict(list)
        for commit in commits:
            for folder in commit_folders.get(commit.id, ()):
                commits_by_folder[folder].append(commit)

        # Per-folder distinct issues (lifetime + recent).
        issues_by_folder: dict[str, set[EntityRef]] = defaultdict(set)
        recent_issues_by_folder: dict[str, set[EntityRef]] = defaultdict(set)
        for commit in commits:
            issues_here = commit_issues.get(commit.id, ())
            if not issues_here:
                continue
            in_recent = (
                cutoff is not None and _commit_in_window(commit, cutoff)
            )
            for folder in commit_folders.get(commit.id, ()):
                for issue_ref in issues_here:
                    issues_by_folder[folder].add(issue_ref)
                    if in_recent:
                        recent_issues_by_folder[folder].add(issue_ref)

        all_distinct_issues: set[EntityRef] = set()
        recent_all_issues: set[EntityRef] = set()
        for commit in commits:
            issues_here = commit_issues.get(commit.id, ())
            if not issues_here:
                continue
            in_recent = (
                cutoff is not None and _commit_in_window(commit, cutoff)
            )
            for issue_ref in issues_here:
                all_distinct_issues.add(issue_ref)
                if in_recent:
                    recent_all_issues.add(issue_ref)

        rows: list[OverviewRow] = []
        component_count = len(commits_by_folder) or 1
        project_mean = round(len(all_distinct_issues) / component_count, 2)
        rows.append(
            _project_row(
                commits, all_distinct_issues, recent_all_issues, cutoff,
                project_mean, commit_issues,
            )
        )
        for folder in sorted(commits_by_folder.keys()):
            rows.append(
                _folder_row(
                    folder,
                    commits_by_folder[folder],
                    issues_by_folder.get(folder, set()),
                    recent_issues_by_folder.get(folder, set()),
                    cutoff, commit_issues,
                )
            )

        return OverviewTable(
            name=self.name,
            entity_kind="component",
            columns=COLUMNS,
            rows=rows,
        )


# ----------------------------------------------------------------------
# Row builders
# ----------------------------------------------------------------------
def _project_row(
    commits: list[Any],
    distinct_issues: set[EntityRef],
    recent_distinct_issues: set[EntityRef],
    cutoff: Optional[Any],
    mean_issues_per_component: float,
    commit_issues: dict[str, tuple[EntityRef, ...]],
) -> OverviewRow:
    cells: dict[str, OverviewCell] = {}
    cells["commits_linked_pct"] = _commits_linked_pct(
        commits, cutoff, commit_issues,
    )
    cells["issues_with_commits_pct"] = _issues_pct_cell(
        len(distinct_issues),
        len(distinct_issues),
        len(recent_distinct_issues) if cutoff is not None else None,
        len(distinct_issues),
    )
    cells["mean_issues_per_component"] = OverviewCell(
        lifetime_value=mean_issues_per_component,
        recent_value=mean_issues_per_component,
        trend_percent=None,
    )
    return OverviewRow(entity_id="(project)", cells=cells)


def _folder_row(
    entity_id: str,
    commits: list[Any],
    lifetime_issues: set[EntityRef],
    recent_issues: set[EntityRef],
    cutoff: Optional[Any],
    commit_issues: dict[str, tuple[EntityRef, ...]],
) -> OverviewRow:
    cells: dict[str, OverviewCell] = {}
    cells["commits_linked_pct"] = _commits_linked_pct(
        commits, cutoff, commit_issues,
    )

    # Universe = same in-folder issue set; mirrors legacy semantics
    # ("of the issues linked to this folder, what % is reachable?").
    universe = len(lifetime_issues)
    lifetime_matched = len(lifetime_issues)
    recent_matched = len(recent_issues) if cutoff is not None else None
    cells["issues_with_commits_pct"] = _issues_pct_cell(
        lifetime_matched, universe, recent_matched, universe,
    )

    lt = len(lifetime_issues)
    rc = len(recent_issues)
    cells["mean_issues_per_component"] = OverviewCell(
        lifetime_value=lt,
        recent_value=rc,
        trend_percent=trend_percent(lt or None, rc or None),
    )
    return OverviewRow(entity_id=entity_id, cells=cells)


# ----------------------------------------------------------------------
# Cell helpers
# ----------------------------------------------------------------------
def _commits_linked_pct(
    commits: list[Any], cutoff: Optional[Any],
    commit_issues: dict[str, tuple[EntityRef, ...]],
) -> OverviewCell:
    if not commits:
        return OverviewCell(
            lifetime_value=None, recent_value=None, trend_percent=None,
        )
    lifetime_total = len(commits)
    lifetime_linked = sum(
        1 for c in commits if commit_issues.get(c.id)
    )
    lifetime_pct = round(100.0 * lifetime_linked / lifetime_total, 2)

    if cutoff is None:
        return OverviewCell(
            lifetime_value=lifetime_pct,
            recent_value=None,
            trend_percent=None,
        )

    recent_commits = [c for c in commits if _commit_in_window(c, cutoff)]
    recent_total = len(recent_commits)
    recent_linked = sum(
        1 for c in recent_commits if commit_issues.get(c.id)
    )
    recent_pct = (
        round(100.0 * recent_linked / recent_total, 2)
        if recent_total else None
    )
    return OverviewCell(
        lifetime_value=lifetime_pct,
        recent_value=recent_pct,
        trend_percent=trend_percent(lifetime_pct, recent_pct),
    )


def _issues_pct_cell(
    lt_matched: int, lt_universe: int,
    rc_matched: Optional[int], rc_universe: int,
) -> OverviewCell:
    if lt_universe <= 0:
        return OverviewCell(
            lifetime_value=None, recent_value=None, trend_percent=None,
        )
    lifetime_pct = round(100.0 * lt_matched / lt_universe, 2)
    if rc_matched is None or rc_universe <= 0:
        return OverviewCell(
            lifetime_value=lifetime_pct,
            recent_value=None,
            trend_percent=None,
        )
    recent_pct = round(100.0 * rc_matched / rc_universe, 2)
    return OverviewCell(
        lifetime_value=lifetime_pct,
        recent_value=recent_pct,
        trend_percent=trend_percent(lifetime_pct, recent_pct),
    )


# ----------------------------------------------------------------------
# Indexes
# ----------------------------------------------------------------------
def _commit_folder_index(graph: Any, commits: list[Any]) -> dict[str, frozenset[str]]:
    """``commit.id -> frozenset[top-folder]`` it touches."""
    changes_by_commit = _changes_by_commit_index(graph)
    out: dict[str, frozenset[str]] = {}
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
        if folders:
            out[commit.id] = frozenset(folders)
    return out


def _commit_issue_index(graph: Any) -> dict[str, tuple[EntityRef, ...]]:
    """``commit.id -> tuple[issue_ref]`` derived from issue_file relations.

    Walks ``relations.of_kind("issue_file")`` and joins each (issue,
    file) edge back to commits via ``changes.by_file``. Mirrors the
    legacy ``commit.issues`` back-pointer (dropped in v2 per Chunks
    4/5).
    """
    relations = getattr(graph, "relations", None)
    if relations is None:
        return {}
    of_kind = getattr(relations, "of_kind", None)
    if of_kind is None:
        return {}

    changes_by_file = _changes_by_file_index(graph)
    by_commit: dict[str, set[EntityRef]] = defaultdict(set)
    for rel in of_kind("issue_file"):
        src = getattr(rel, "source", None)
        tgt = getattr(rel, "target", None)
        if (
            src is None or tgt is None
            or src.kind != EntityKind.ISSUE
            or tgt.kind != EntityKind.FILE
        ):
            continue
        for change in changes_by_file(tgt):
            commit_ref = getattr(change, "commit_ref", None)
            if commit_ref is None:
                continue
            by_commit[commit_ref.id].add(src)

    return {cid: tuple(refs) for cid, refs in by_commit.items()}


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


__all__ = ["FeatureTraceabilityTableBuilder", "COLUMNS"]
