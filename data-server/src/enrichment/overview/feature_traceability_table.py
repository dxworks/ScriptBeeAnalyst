"""Feature traceability overview — Jira ↔ Git linker visibility.

Answers "for this scope, what fraction of code activity is anchored to an
issue, and how many issues map to it?". Requires the project linker to have
populated `commit.issues` / `issue.git_commits` — falls back gracefully when
those bridges are empty.

Columns (lifetime + recent + trend% where applicable):
  - commits_linked_pct        — % of commits in scope with at least one issue.
  - issues_with_commits_pct   — % of in-scope issues whose linked commits land
                                in this scope (lifetime: any; recent: at least
                                one linked commit inside the recent window).
  - mean_issues_per_component — DECISION: per-row "distinct issues touching
                                this component" rather than a project-wide
                                mean repeated on every row. The (project) row
                                still reports the cross-component mean
                                (total distinct issues / component_count) so
                                callers can tell project-vs-component apart.

Rows: synthetic '(project)' aggregate + one per top-level folder.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from src.enrichment.components.resolver import top_folder_of
from src.enrichment.models import OverviewCell, OverviewRow, OverviewTable
from src.enrichment.overview.builder import constant_cell
from src.enrichment.recent_window import ensure_aware, trend_percent
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


COLUMNS = [
    "commits_linked_pct",
    "issues_with_commits_pct",
    "mean_issues_per_component",
]


class FeatureTraceabilityTableBuilder:

    NAME = "feature_traceability"
    ENTITY_KIND = "component"

    def build(self, ctx: TaggingContext, tags_by_entity: dict) -> OverviewTable:
        git = ctx.graph_data.get("git")
        rows: list[OverviewRow] = []
        if git is None:
            return OverviewTable(name=self.NAME, entity_kind="component", columns=COLUMNS, rows=rows)

        cutoff = ctx.recent_cutoff
        all_commits = list(git.git_commit_registry.all)
        commits_by_folder = _bucket_commits_by_top_folder(all_commits)

        # Distinct-issues-by-folder is built once so the (project) row's
        # cross-component mean can be derived without re-walking the graph.
        issues_by_folder = _distinct_issues_by_top_folder(all_commits)
        recent_issues_by_folder = _distinct_issues_by_top_folder(
            all_commits, cutoff=cutoff,
        )

        # Project row: classic "any commit in the project that touches an issue"
        # plus the cross-component mean.
        component_count = len(issues_by_folder) or 1
        all_distinct_issues = _distinct_issues(all_commits)
        project_mean = round(len(all_distinct_issues) / component_count, 2)
        rows.append(self._project_row(
            all_commits, all_distinct_issues, cutoff, project_mean,
        ))

        for folder in sorted(commits_by_folder.keys()):
            rows.append(self._folder_row(
                folder,
                commits_by_folder[folder],
                issues_by_folder.get(folder, set()),
                recent_issues_by_folder.get(folder, set()),
                cutoff,
            ))

        return OverviewTable(
            name=self.NAME,
            entity_kind="component",
            columns=COLUMNS,
            rows=rows,
        )

    def _project_row(
        self,
        commits: list,
        distinct_issues: set,
        cutoff,
        mean_issues_per_component: float,
    ) -> OverviewRow:
        cells: dict[str, OverviewCell] = {}
        cells["commits_linked_pct"] = _commits_linked_pct(commits, cutoff)

        # issues_with_commits_pct at project scope = any issue with at least
        # one linked commit (lifetime) / at least one linked commit in the
        # recent window (recent), out of all issues with ≥1 linked commit.
        cells["issues_with_commits_pct"] = _issues_linked_pct_for_scope(
            distinct_issues, distinct_issues, cutoff,
        )

        cells["mean_issues_per_component"] = constant_cell(mean_issues_per_component)
        return OverviewRow(entity_id="(project)", cells=cells)

    def _folder_row(
        self,
        entity_id: str,
        commits: list,
        lifetime_issues: set,
        recent_issues: set,
        cutoff,
    ) -> OverviewRow:
        cells: dict[str, OverviewCell] = {}
        cells["commits_linked_pct"] = _commits_linked_pct(commits, cutoff)
        cells["issues_with_commits_pct"] = _issues_linked_pct_for_scope(
            lifetime_issues, lifetime_issues, cutoff,
            recent_subset=recent_issues,
        )
        # Per-component meaning: distinct issues touching this component.
        cells["mean_issues_per_component"] = OverviewCell(
            lifetime_value=len(lifetime_issues),
            recent_value=len(recent_issues),
            trend_percent=trend_percent(
                len(lifetime_issues) or None,
                len(recent_issues) or None,
            ),
        )
        return OverviewRow(entity_id=entity_id, cells=cells)


# ── helpers ────────────────────────────────────────────────────────────────────

def _commit_issues(commit) -> list:
    return getattr(commit, "issues", None) or []


def _issue_commits(issue) -> list:
    return getattr(issue, "git_commits", None) or []


def _commits_linked_pct(commits: list, cutoff) -> OverviewCell:
    if not commits:
        return OverviewCell(lifetime_value=None, recent_value=None, trend_percent=None)
    lifetime_total = len(commits)
    lifetime_linked = sum(1 for c in commits if _commit_issues(c))

    if cutoff is None:
        recent_pct = None
        lifetime_pct = round(100.0 * lifetime_linked / lifetime_total, 2)
        return OverviewCell(
            lifetime_value=lifetime_pct,
            recent_value=recent_pct,
            trend_percent=None,
        )

    recent_commits = [
        c for c in commits
        if (d := ensure_aware(getattr(c, "author_date", None))) is not None and d >= cutoff
    ]
    recent_total = len(recent_commits)
    recent_linked = sum(1 for c in recent_commits if _commit_issues(c))

    lifetime_pct = round(100.0 * lifetime_linked / lifetime_total, 2)
    recent_pct = round(100.0 * recent_linked / recent_total, 2) if recent_total else None
    return OverviewCell(
        lifetime_value=lifetime_pct,
        recent_value=recent_pct,
        trend_percent=trend_percent(lifetime_pct, recent_pct),
    )


def _issues_linked_pct_for_scope(
    in_scope_issues,
    universe_issues,
    cutoff,
    recent_subset: Optional[set] = None,
) -> OverviewCell:
    """Fraction of universe_issues that are (a) in scope and (b) have ≥1 linked commit.

    Lifetime: in_scope_issues with ≥1 linked commit / |universe_issues|.
    Recent: those whose linked commits include one inside the cutoff window
    (or `recent_subset` if pre-computed).
    """
    universe = list(universe_issues)
    if not universe:
        return OverviewCell(lifetime_value=None, recent_value=None, trend_percent=None)

    lt_matched = sum(1 for i in in_scope_issues if _issue_commits(i))
    lifetime_pct = round(100.0 * lt_matched / len(universe), 2)

    if cutoff is None:
        return OverviewCell(
            lifetime_value=lifetime_pct,
            recent_value=None,
            trend_percent=None,
        )

    if recent_subset is None:
        recent_subset = set()
        for issue in in_scope_issues:
            for c in _issue_commits(issue):
                d = ensure_aware(getattr(c, "author_date", None))
                if d is not None and d >= cutoff:
                    recent_subset.add(issue)
                    break
    recent_pct = round(100.0 * len(recent_subset) / len(universe), 2)
    return OverviewCell(
        lifetime_value=lifetime_pct,
        recent_value=recent_pct,
        trend_percent=trend_percent(lifetime_pct, recent_pct),
    )


def _bucket_commits_by_top_folder(commits: list) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for commit in commits:
        folders = set()
        for ch in getattr(commit, "changes", None) or []:
            f = getattr(ch, "file", None)
            if f is None:
                continue
            top = top_folder_of(_file_id(f))
            if top is None:
                continue
            folders.add(top)
        for folder in folders:
            out[folder].append(commit)
    return dict(out)


def _distinct_issues(commits: list) -> set:
    out = set()
    for c in commits:
        for issue in _commit_issues(c):
            out.add(issue)
    return out


def _distinct_issues_by_top_folder(commits: list, cutoff=None) -> dict[str, set]:
    out: dict[str, set] = defaultdict(set)
    for commit in commits:
        if cutoff is not None:
            d = ensure_aware(getattr(commit, "author_date", None))
            if d is None or d < cutoff:
                continue
        issues = _commit_issues(commit)
        if not issues:
            continue
        folders = set()
        for ch in getattr(commit, "changes", None) or []:
            f = getattr(ch, "file", None)
            if f is None:
                continue
            top = top_folder_of(_file_id(f))
            if top is None:
                continue
            folders.add(top)
        for folder in folders:
            for issue in issues:
                out[folder].add(issue)
    return dict(out)
