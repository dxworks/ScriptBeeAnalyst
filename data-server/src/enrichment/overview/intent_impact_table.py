"""Intent / Impact overview — per Issue type.

Rows: one per distinct issue type name + a synthetic '(project)' aggregate.

Columns (lifetime + recent + trend%):
  - issue_count
  - linked_commits      — distinct commits linked to issues of this type
  - linked_churn        — sum of added+deleted across those commits
  - avg_churn_per_issue — linked_churn / issue_count
  - linked_files        — distinct files touched by those commits
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from src.enrichment.models import OverviewCell, OverviewRow, OverviewTable
from src.enrichment.recent_window import ensure_aware, trend_percent
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


COLUMNS = [
    "issue_count",
    "linked_commits",
    "linked_churn",
    "avg_churn_per_issue",
    "linked_files",
]


class IntentImpactTableBuilder:

    NAME = "intent_impact"

    def build(self, ctx: TaggingContext, tags_by_entity: dict) -> OverviewTable:
        jira = ctx.graph_data.get("jira")
        rows: list[OverviewRow] = []
        if jira is None:
            return OverviewTable(name=self.NAME, entity_kind="issue", columns=COLUMNS, rows=rows)

        cutoff = ctx.recent_cutoff

        issues_by_type: dict[str, list] = defaultdict(list)
        for issue in jira.issue_registry.all:
            types = getattr(issue, "issue_types", None) or []
            for it in types:
                name = getattr(it, "name", None)
                if name:
                    issues_by_type[name].append(issue)

        rows.append(self._row_for(
            "(project)",
            list(jira.issue_registry.all),
            cutoff,
        ))
        for type_name in sorted(issues_by_type.keys()):
            rows.append(self._row_for(type_name, issues_by_type[type_name], cutoff))

        return OverviewTable(
            name=self.NAME,
            entity_kind="issue",
            columns=COLUMNS,
            rows=rows,
        )

    def _row_for(self, entity_id, issues, cutoff) -> OverviewRow:
        lifetime_commits: set[str] = set()
        recent_commits: set[str] = set()
        lifetime_files: set[str] = set()
        recent_files: set[str] = set()
        lifetime_churn = 0
        recent_churn = 0
        recent_issues = 0

        for issue in issues:
            issue_in_recent = False
            for c in getattr(issue, "git_commits", None) or []:
                cid = getattr(c, "id", None)
                d = ensure_aware(getattr(c, "author_date", None))
                in_recent = cutoff is not None and d is not None and d >= cutoff
                if in_recent:
                    issue_in_recent = True
                if cid and cid in lifetime_commits:
                    # Already counted commit-level metrics for this commit.
                    if in_recent and cid not in recent_commits:
                        recent_commits.add(cid)
                    continue
                if cid:
                    lifetime_commits.add(cid)
                    if in_recent:
                        recent_commits.add(cid)
                for ch in getattr(c, "changes", None) or []:
                    f = getattr(ch, "file", None)
                    fid = _file_id(f) if f is not None else None
                    churn = _change_churn(ch)
                    lifetime_churn += churn
                    if fid:
                        lifetime_files.add(fid)
                    if in_recent:
                        recent_churn += churn
                        if fid:
                            recent_files.add(fid)
            if issue_in_recent:
                recent_issues += 1

        cells: dict[str, OverviewCell] = {}
        cells["issue_count"] = OverviewCell(
            lifetime_value=len(issues),
            recent_value=recent_issues,
            trend_percent=trend_percent(len(issues) or None, recent_issues or None),
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
            trend_percent=trend_percent(lifetime_churn or None, recent_churn or None),
        )
        cells["avg_churn_per_issue"] = OverviewCell(
            lifetime_value=_avg(lifetime_churn, len(issues)),
            recent_value=_avg(recent_churn, recent_issues),
            trend_percent=trend_percent(
                _avg(lifetime_churn, len(issues)),
                _avg(recent_churn, recent_issues),
            ),
        )
        cells["linked_files"] = OverviewCell(
            lifetime_value=len(lifetime_files),
            recent_value=len(recent_files),
            trend_percent=None,
        )

        return OverviewRow(entity_id=entity_id, cells=cells)


def _change_churn(change) -> int:
    total = 0
    for hunk in getattr(change, "hunks", None) or []:
        total += len(getattr(hunk, "added_lines", []) or [])
        total += len(getattr(hunk, "deleted_lines", []) or [])
    return total if total > 0 else 1


def _avg(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 2)
