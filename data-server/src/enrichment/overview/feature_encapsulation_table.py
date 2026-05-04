"""Feature encapsulation overview — per-component encapsulation metrics.

Implements §7 (FeatureEncapsulationOverview) of
communication/B2_codeframe/index_step_general.md.

dx port (FeatureEncapsulationOverview.java:13–94). For each component:
  - file_count                   — files in the component.
  - source_loc_kloc              — Lizard NLOC summed across the component, in
                                    thousands (None when no Lizard data).
  - commit_count                 — distinct commits touching any file.
  - recent_commit_count          — same, restricted to the recent window.
  - wide_commit_pct              — % of commits touching >=
                                    cfg.feature_encapsulation_wide_files_min files
                                    that also touch a file in this component.
  - deep_commit_pct              — % of commits with churn >=
                                    cfg.feature_encapsulation_deep_churn_min lines.
  - high_impact_task_count       — distinct issues touching >=
                                    cfg.feature_encapsulation_high_impact_files_min files
                                    overall and at least one file in this component.
  - scattered_task_count         — distinct issues touching files in >=
                                    cfg.feature_encapsulation_scattered_components_min
                                    components AND at least one in this component.

Wide / deep / high-impact / scattered thresholds live on EnrichmentConfig so
the agent can re-tune via /reenrich.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from src.enrichment.components.resolver import ComponentResolver
from src.enrichment.models import (
    Component,
    OverviewCell,
    OverviewRow,
    OverviewTable,
)
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


COLUMNS = [
    "file_count",
    "source_loc_kloc",
    "commit_count",
    "recent_commit_count",
    "wide_commit_pct",
    "deep_commit_pct",
    "high_impact_task_count",
    "scattered_task_count",
]


class FeatureEncapsulationTableBuilder:

    NAME = "feature_encapsulation"
    ENTITY_KIND = "component"

    def build(
        self,
        ctx: TaggingContext,
        tags_by_entity: dict,
        components: list[Component],
        resolver: ComponentResolver,
    ) -> OverviewTable:
        git = ctx.graph_data.get("git")
        rows: list[OverviewRow] = []
        if git is None:
            return OverviewTable(
                name=self.NAME, entity_kind="component",
                columns=COLUMNS, rows=rows,
            )

        cfg = ctx.config
        cutoff = ctx.recent_cutoff

        # Pre-compute file -> component (so each commit can be assigned to
        # touching components without re-walking the resolver O(N) times).
        files_by_component: dict[str, list] = defaultdict(list)
        component_of_path: dict[str, str] = {}
        for f in git.file_registry.all:
            fid = _file_id(f)
            if not fid:
                continue
            comp = resolver.resolve(fid)
            if comp is None:
                continue
            files_by_component[comp].append(f)
            component_of_path[fid] = comp

        # Pre-compute commit-level signals once.
        commit_index = _index_commits(
            git.git_commit_registry.all,
            component_of_path,
            wide_threshold=cfg.feature_encapsulation_wide_files_min,
            deep_threshold=cfg.feature_encapsulation_deep_churn_min,
        )

        # Issue-level signals (high-impact + scattered) require Jira-side data.
        issue_index = _index_issues(
            ctx.graph_data.get("jira"),
            component_of_path,
            high_impact_files_min=cfg.feature_encapsulation_high_impact_files_min,
            scattered_components_min=cfg.feature_encapsulation_scattered_components_min,
        )

        all_components = list(component_of_path.values())
        all_components_set = set(all_components)

        rows.append(self._project_row(
            git, ctx, cutoff, commit_index, issue_index,
            all_components_set,
        ))
        for c in components:
            rows.append(self._component_row(
                c.name,
                files_by_component.get(c.name, []),
                cutoff, ctx, commit_index, issue_index,
            ))

        return OverviewTable(
            name=self.NAME,
            entity_kind="component",
            columns=COLUMNS,
            rows=rows,
        )

    def _project_row(
        self, git, ctx, cutoff, commit_index, issue_index, all_components_set,
    ) -> OverviewRow:
        all_files = list(git.file_registry.all)
        cells = self._cells_for(
            all_files, cutoff, ctx, commit_index, issue_index,
            scope=None,
        )
        return OverviewRow(entity_id="(project)", cells=cells)

    def _component_row(
        self, name, files, cutoff, ctx, commit_index, issue_index,
    ) -> OverviewRow:
        cells = self._cells_for(
            files, cutoff, ctx, commit_index, issue_index, scope=name,
        )
        return OverviewRow(entity_id=name, cells=cells)

    def _cells_for(
        self, files, cutoff, ctx, commit_index, issue_index, scope: Optional[str],
    ) -> dict[str, OverviewCell]:
        cells: dict[str, OverviewCell] = {}

        cells["file_count"] = OverviewCell(
            lifetime_value=len(files),
            recent_value=len(files),
            trend_percent=None,
        )

        # source_loc_kloc — Lizard NLOC sum / 1000.
        loc_total = 0
        files_with_loc = 0
        for f in files:
            fid = _file_id(f)
            if fid is None:
                continue
            metric = ctx.file_metric_map.get(fid)
            if metric is not None:
                loc_total += metric.sum_nloc
                files_with_loc += 1
        kloc = round(loc_total / 1000.0, 2) if files_with_loc > 0 else None
        cells["source_loc_kloc"] = OverviewCell(
            lifetime_value=kloc, recent_value=kloc, trend_percent=None,
        )

        # Commit-level columns — gather commits touching this component (or all).
        commits_in_scope = _commits_in_scope(commit_index["all"], scope)
        recent_in_scope = [
            c for c in commits_in_scope
            if cutoff is not None
            and (d := ensure_aware(getattr(c["raw"], "author_date", None))) is not None
            and d >= cutoff
        ]
        cells["commit_count"] = OverviewCell(
            lifetime_value=len(commits_in_scope),
            recent_value=len(recent_in_scope),
            trend_percent=None,
        )
        cells["recent_commit_count"] = OverviewCell(
            lifetime_value=len(recent_in_scope),
            recent_value=len(recent_in_scope),
            trend_percent=None,
        )

        wide_pct = _percent(
            sum(1 for c in commits_in_scope if c["wide"]), len(commits_in_scope),
        )
        deep_pct = _percent(
            sum(1 for c in commits_in_scope if c["deep"]), len(commits_in_scope),
        )
        cells["wide_commit_pct"] = OverviewCell(
            lifetime_value=wide_pct, recent_value=wide_pct, trend_percent=None,
        )
        cells["deep_commit_pct"] = OverviewCell(
            lifetime_value=deep_pct, recent_value=deep_pct, trend_percent=None,
        )

        # Issue-level — restrict to issues whose touched files include this scope.
        if scope is None:
            high_impact = len(issue_index["high_impact"])
            scattered = len(issue_index["scattered"])
        else:
            high_impact = sum(
                1 for issue in issue_index["high_impact"]
                if scope in issue_index["components_by_issue"].get(id(issue), set())
            )
            scattered = sum(
                1 for issue in issue_index["scattered"]
                if scope in issue_index["components_by_issue"].get(id(issue), set())
            )
        cells["high_impact_task_count"] = OverviewCell(
            lifetime_value=high_impact, recent_value=high_impact, trend_percent=None,
        )
        cells["scattered_task_count"] = OverviewCell(
            lifetime_value=scattered, recent_value=scattered, trend_percent=None,
        )

        return cells


# ── helpers ────────────────────────────────────────────────────────────────────

def _index_commits(
    commits, component_of_path: dict[str, str],
    *, wide_threshold: int, deep_threshold: int,
) -> dict:
    """Pre-compute per-commit signals so component rows iterate in O(N_commits)."""
    indexed: list[dict] = []
    for c in commits:
        changes = getattr(c, "changes", None) or []
        files_touched: set[str] = set()
        churn = 0
        for ch in changes:
            f = getattr(ch, "file", None)
            if f is None:
                continue
            fid = _file_id(f)
            if fid:
                files_touched.add(fid)
            for hunk in getattr(ch, "hunks", None) or []:
                churn += len(getattr(hunk, "added_lines", []) or [])
                churn += len(getattr(hunk, "deleted_lines", []) or [])
        components_touched = {
            component_of_path[fid]
            for fid in files_touched
            if fid in component_of_path
        }
        indexed.append({
            "raw": c,
            "files": files_touched,
            "components": components_touched,
            "churn": churn,
            "wide": len(files_touched) >= wide_threshold,
            "deep": churn >= deep_threshold,
        })
    return {"all": indexed}


def _commits_in_scope(indexed: list[dict], scope: Optional[str]) -> list[dict]:
    if scope is None:
        return indexed
    return [c for c in indexed if scope in c["components"]]


def _index_issues(
    jira, component_of_path: dict[str, str],
    *, high_impact_files_min: int, scattered_components_min: int,
) -> dict:
    """Pre-compute high-impact + scattered issue sets keyed by component."""
    high_impact: list = []
    scattered: list = []
    components_by_issue: dict[int, set[str]] = {}
    if jira is None:
        return {"high_impact": [], "scattered": [], "components_by_issue": {}}

    for issue in jira.issue_registry.all:
        commits = getattr(issue, "git_commits", None) or []
        if not commits:
            continue
        files_touched: set[str] = set()
        for c in commits:
            for ch in getattr(c, "changes", None) or []:
                f = getattr(ch, "file", None)
                if f is None:
                    continue
                fid = _file_id(f)
                if fid:
                    files_touched.add(fid)
        components = {
            component_of_path[fid]
            for fid in files_touched
            if fid in component_of_path
        }
        components_by_issue[id(issue)] = components

        if len(files_touched) >= high_impact_files_min:
            high_impact.append(issue)
        if len(components) >= scattered_components_min:
            scattered.append(issue)

    return {
        "high_impact": high_impact,
        "scattered": scattered,
        "components_by_issue": components_by_issue,
    }


def _percent(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 2)
