"""Intent-impact overview — v2 port (Chunk 18).

Port of legacy ``src/enrichment/overview/intent_impact_table.py``. Rolls
issue activity up per issue-type label and crosses it with the impact
signals carried by linked commits + structural anomalies.

Reads:

* ``graph.issues``                              — issue population.
* ``graph.classifiers`` (dimension ``issue.type``) — Chunk-16 native
  Jira type label, used as the per-row bucket.
* ``graph.relations.of_kind("issue_file")``     — issue ↔ file linkage
  built by :class:`IssueFileBuilder` (Chunk 7). Walked back through
  ``changes.by_file`` to identify the linking commits + churn.
* ``graph.commits`` / ``graph.changes`` / ``graph.hunks`` — for the
  per-commit churn rollup behind the ``linked_churn`` /
  ``avg_churn_per_issue`` cells.
* ``graph.traits`` (name ``anomaly.structuring.TasksBottleneck``) —
  Chunk-15b issue-targeted anomaly, contributes the
  ``bottleneck_count`` column so a (Bug, Story, …) row can surface
  intent labels under structural pressure.

Columns (lifetime + recent + trend% where applicable):

  issue_count, linked_commits, linked_churn, avg_churn_per_issue,
  linked_files, bottleneck_count.

Rows: synthetic ``(project)`` aggregate + one per distinct
``issue.type`` classifier value.
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, ClassVar, Optional

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
    "issue_count",
    "linked_commits",
    "linked_churn",
    "avg_churn_per_issue",
    "linked_files",
    "bottleneck_count",
]


_TYPE_DIM = "issue.type"
_BOTTLENECK_TRAIT = "anomaly.structuring.TasksBottleneck"


@OVERVIEWS.register
class IntentImpactTableBuilder(OverviewTableBuilder):
    """One row per ``issue.type`` value + a synthetic ``(project)`` aggregate."""

    name: ClassVar[str] = "intent_impact"

    def build(self, graph: "Graph", config: Any) -> OverviewTable:
        issues_reg = getattr(graph, "issues", None)
        if issues_reg is None:
            return OverviewTable(
                name=self.name, entity_kind="issue",
                columns=COLUMNS, rows=[],
            )
        try:
            issues = list(issues_reg)
        except TypeError:
            issues = []

        cutoff = _resolve_recent_cutoff(graph)
        type_by_issue_id = _classifier_by_issue_id(graph, _TYPE_DIM)
        bottleneck_issue_ids = _issue_ids_with_trait(graph, _BOTTLENECK_TRAIT)

        commits_by_issue = _commits_by_issue_id(graph)
        commits_get = _entity_by_id(getattr(graph, "commits", None))
        changes_by_commit = _changes_by_commit_index(graph)
        hunks_by_change = _hunks_by_change_index(graph)

        issues_by_type: dict[str, list[Any]] = defaultdict(list)
        for issue in issues:
            type_name = type_by_issue_id.get(issue.id)
            if type_name:
                issues_by_type[type_name].append(issue)

        rows: list[OverviewRow] = [
            _row_for(
                "(project)", issues, cutoff,
                commits_by_issue, commits_get,
                changes_by_commit, hunks_by_change,
                bottleneck_issue_ids,
            )
        ]
        for type_name in sorted(issues_by_type.keys()):
            rows.append(
                _row_for(
                    type_name, issues_by_type[type_name], cutoff,
                    commits_by_issue, commits_get,
                    changes_by_commit, hunks_by_change,
                    bottleneck_issue_ids,
                )
            )

        return OverviewTable(
            name=self.name,
            entity_kind="issue",
            columns=COLUMNS,
            rows=rows,
        )


# ----------------------------------------------------------------------
# Per-row aggregation
# ----------------------------------------------------------------------
def _row_for(
    entity_id: str,
    issues: list[Any],
    cutoff: Optional[Any],
    commits_by_issue,
    commits_get,
    changes_by_commit,
    hunks_by_change,
    bottleneck_issue_ids: set[str],
) -> OverviewRow:
    lifetime_commits: set[str] = set()
    recent_commits: set[str] = set()
    lifetime_files: set[Any] = set()
    recent_files: set[Any] = set()
    lifetime_churn = 0
    recent_churn = 0
    recent_issues = 0
    bottleneck_count = 0

    for issue in issues:
        if issue.id in bottleneck_issue_ids:
            bottleneck_count += 1
        issue_in_recent = False

        for commit in commits_by_issue(issue.ref()):
            cid = getattr(commit, "id", None)
            if cid is None:
                continue
            d = ensure_aware(getattr(commit, "author_date", None))
            in_recent = (
                cutoff is not None and d is not None and d >= cutoff
            )
            if in_recent:
                issue_in_recent = True
            already_seen = cid in lifetime_commits
            lifetime_commits.add(cid)
            if in_recent:
                recent_commits.add(cid)
            if already_seen:
                # Don't double-count file/churn for the same commit.
                continue
            for change in changes_by_commit(commit.ref()):
                fref = getattr(change, "file_ref", None)
                churn = _change_churn(change, hunks_by_change)
                lifetime_churn += churn
                if fref is not None:
                    lifetime_files.add(fref)
                if in_recent:
                    recent_churn += churn
                    if fref is not None:
                        recent_files.add(fref)

        if issue_in_recent:
            recent_issues += 1

    cells: dict[str, OverviewCell] = {}
    cells["issue_count"] = OverviewCell(
        lifetime_value=len(issues),
        recent_value=recent_issues,
        trend_percent=trend_percent(
            len(issues) or None, recent_issues or None,
        ),
    )
    cells["linked_commits"] = OverviewCell(
        lifetime_value=len(lifetime_commits),
        recent_value=len(recent_commits),
        trend_percent=trend_percent(
            len(lifetime_commits) or None,
            len(recent_commits) or None,
        ),
    )
    cells["linked_churn"] = OverviewCell(
        lifetime_value=lifetime_churn,
        recent_value=recent_churn,
        trend_percent=trend_percent(
            lifetime_churn or None, recent_churn or None,
        ),
    )
    lt_avg = _avg(lifetime_churn, len(issues))
    rc_avg = _avg(recent_churn, recent_issues)
    cells["avg_churn_per_issue"] = OverviewCell(
        lifetime_value=lt_avg,
        recent_value=rc_avg,
        trend_percent=trend_percent(lt_avg, rc_avg),
    )
    cells["linked_files"] = OverviewCell(
        lifetime_value=len(lifetime_files),
        recent_value=len(recent_files),
        trend_percent=None,
    )
    cells["bottleneck_count"] = OverviewCell(
        lifetime_value=bottleneck_count,
        recent_value=bottleneck_count,
        trend_percent=None,
    )
    return OverviewRow(entity_id=entity_id, cells=cells)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _classifier_by_issue_id(graph: Any, dimension: str) -> dict[str, str]:
    classifiers = getattr(graph, "classifiers", None)
    if classifiers is None:
        return {}
    of_dimension = getattr(classifiers, "of_dimension", None)
    if of_dimension is None:
        return {}
    out: dict[str, str] = {}
    for cls_obj in of_dimension(dimension):
        target: EntityRef = cls_obj.target
        if target.kind != EntityKind.ISSUE:
            continue
        out[target.id] = cls_obj.value
    return out


def _issue_ids_with_trait(graph: Any, trait_name: str) -> set[str]:
    traits = getattr(graph, "traits", None)
    if traits is None:
        return set()
    of_name = getattr(traits, "of_name", None)
    if of_name is None:
        return set()
    out: set[str] = set()
    for t in of_name(trait_name):
        target: EntityRef = t.target
        if target.kind != EntityKind.ISSUE:
            continue
        out.add(target.id)
    return out


def _commits_by_issue_id(graph: Any):
    """Return ``issue_ref -> list[Commit]`` resolved via ``issue_file`` relations.

    Walks ``relations.of_kind("issue_file")`` once and, for each
    file linked to an issue, dereferences the changes touching that file
    to recover the linking commits. Mirrors the legacy
    ``issue.git_commits`` back-pointer (dropped in v2 per Chunks 4/5).
    """
    relations = getattr(graph, "relations", None)
    if relations is None:
        return lambda _issue_ref: []
    of_kind = getattr(relations, "of_kind", None)
    if of_kind is None:
        return lambda _issue_ref: []

    changes_by_file = _changes_by_file_index(graph)
    commits_get = _entity_by_id(getattr(graph, "commits", None))

    by_issue_ref: dict[EntityRef, list[Any]] = defaultdict(list)
    seen: dict[EntityRef, set[str]] = defaultdict(set)
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
            cid = commit_ref.id
            if cid in seen[src]:
                continue
            commit = commits_get(cid)
            if commit is None:
                continue
            seen[src].add(cid)
            by_issue_ref[src].append(commit)

    return lambda issue_ref: by_issue_ref.get(issue_ref, [])


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


def _changes_by_commit_index(graph: Any):
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _commit_ref: []
    by_commit = getattr(changes, "by_commit", None)
    if by_commit is not None:
        return lambda commit_ref: by_commit[commit_ref]

    def scan(commit_ref):
        return [ch for ch in changes if ch.commit_ref == commit_ref]

    return scan


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


def _entity_by_id(reg: Any):
    if reg is None:
        return lambda _id: None
    get = getattr(reg, "get", None)
    if get is None:
        return lambda _id: None
    return get


def _change_churn(change: Any, hunks_by_change) -> int:
    total = 0
    for hunk in hunks_by_change(change.ref()):
        total += len(getattr(hunk, "added_lines", []) or [])
        total += len(getattr(hunk, "deleted_lines", []) or [])
    return total if total > 0 else 1


def _avg(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 2)


def _resolve_recent_cutoff(graph: Any) -> Optional[Any]:
    explicit = getattr(graph, "recent_cutoff", None)
    if explicit is not None:
        return ensure_aware(explicit)
    return None


__all__ = ["IntentImpactTableBuilder", "COLUMNS"]
