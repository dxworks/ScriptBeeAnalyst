"""Feature-encapsulation overview — v2 port (Chunk 18).

Port of legacy
``src/enrichment/overview/feature_encapsulation_table.py``. Per-component
encapsulation metrics; same source-of-truth thresholds on
:class:`EnrichmentConfig` (``feature_encapsulation_*``).

For each component:

* ``file_count``                — files in the component.
* ``source_loc_kloc``           — Lizard ``sum_nloc`` summed, divided by
  1000. ``None`` when no Lizard data.
* ``commit_count``              — distinct commits touching any file in
  the component.
* ``recent_commit_count``       — same, restricted to ``recent_cutoff``.
* ``wide_commit_pct``           — % of commits touching ≥ ``cfg.feature_
  encapsulation_wide_files_min`` files that also touch a file in this
  component (project-wide denominator at the project row).
* ``deep_commit_pct``           — % of commits with churn ≥ ``cfg.feature_
  encapsulation_deep_churn_min`` lines.
* ``high_impact_task_count``    — distinct issues touching ≥ ``cfg.feature_
  encapsulation_high_impact_files_min`` files AND ≥ 1 in this component.
* ``scattered_task_count``      — distinct issues touching ≥ ``cfg.feature_
  encapsulation_scattered_components_min`` components AND ≥ 1 in this
  component.

Reads:

* ``graph.commits`` + ``graph.changes`` + ``graph.hunks`` — per-commit
  file-touch + churn rollups.
* ``graph.relations.of_kind("component_membership")`` — file ↔
  component edges (Chunk-14 ``ComponentResolverMetric``).
* ``graph.components`` — explicit :class:`Component` entities (pins row
  ordering; falls back to membership-derived names alphabetical).
* ``graph.relations.of_kind("issue_file")`` — issue ↔ file edges, walked
  back into linking commits for issue-level signals.
* ``graph.file_metrics`` — Lizard NLOC for ``source_loc_kloc``.

Rows: synthetic ``(project)`` aggregate + one per resolved component.
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
from src.enrichment.recent_window import ensure_aware

if TYPE_CHECKING:
    from src.common.kernel import Graph


COLUMNS: list[str] = [
    "file_count",
    "source_loc_kloc",
    "commit_count",
    "recent_commit_count",
    "wide_commit_pct",
    "deep_commit_pct",
    "high_impact_task_count",
    "scattered_task_count",
]


_DEFAULTS: dict[str, int] = {
    "feature_encapsulation_wide_files_min": 20,
    "feature_encapsulation_deep_churn_min": 500,
    "feature_encapsulation_high_impact_files_min": 10,
    "feature_encapsulation_scattered_components_min": 3,
}


@OVERVIEWS.register
class FeatureEncapsulationTableBuilder(OverviewTableBuilder):
    """One row per component + a synthetic ``(project)`` aggregate."""

    name: ClassVar[str] = "feature_encapsulation"

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
        wide_min = _cfg_int(config, "feature_encapsulation_wide_files_min")
        deep_min = _cfg_int(config, "feature_encapsulation_deep_churn_min")
        hi_min = _cfg_int(config, "feature_encapsulation_high_impact_files_min")
        scat_min = _cfg_int(config, "feature_encapsulation_scattered_components_min")

        component_of_file = _component_of_file(graph)
        ordered_components = _ordered_component_names(graph, component_of_file)

        files_by_component: dict[str, list[Any]] = defaultdict(list)
        for f in files:
            comp = component_of_file.get(f.id)
            if comp is None:
                continue
            files_by_component[comp].append(f)

        # Commit-level indexing — components touched, file count, churn.
        commit_index = _index_commits(
            graph, component_of_file, wide_min, deep_min, cutoff,
        )

        # Issue-level indexing — high-impact + scattered.
        issue_index = _index_issues(
            graph, component_of_file, hi_min, scat_min,
        )

        nloc_by_file_id = _nloc_index(graph)

        rows: list[OverviewRow] = [
            _row_for(
                "(project)", files, commit_index, issue_index,
                scope=None, nloc_by_file_id=nloc_by_file_id,
            )
        ]
        for name in ordered_components:
            rows.append(
                _row_for(
                    name,
                    files_by_component.get(name, []),
                    commit_index, issue_index,
                    scope=name, nloc_by_file_id=nloc_by_file_id,
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
    commit_index: list[dict[str, Any]],
    issue_index: dict[str, Any],
    *,
    scope: Optional[str],
    nloc_by_file_id: dict[str, float],
) -> OverviewRow:
    # File-count + LOC are direct off the in-scope files.
    loc_total = 0.0
    files_with_loc = 0
    for f in files:
        nloc = nloc_by_file_id.get(f.id)
        if nloc is not None:
            loc_total += nloc
            files_with_loc += 1
    kloc = round(loc_total / 1000.0, 2) if files_with_loc > 0 else None

    # Commit-level columns — scope filter applied here.
    commits_in_scope = [
        c for c in commit_index if scope is None or scope in c["components"]
    ]
    recent_in_scope = [c for c in commits_in_scope if c["in_recent"]]
    wide_pct = _percent(
        sum(1 for c in commits_in_scope if c["wide"]),
        len(commits_in_scope),
    )
    deep_pct = _percent(
        sum(1 for c in commits_in_scope if c["deep"]),
        len(commits_in_scope),
    )

    if scope is None:
        high_impact = len(issue_index["high_impact"])
        scattered = len(issue_index["scattered"])
    else:
        components_by_issue = issue_index["components_by_issue"]
        high_impact = sum(
            1 for iref in issue_index["high_impact"]
            if scope in components_by_issue.get(iref, frozenset())
        )
        scattered = sum(
            1 for iref in issue_index["scattered"]
            if scope in components_by_issue.get(iref, frozenset())
        )

    cells: dict[str, OverviewCell] = {
        "file_count": OverviewCell(
            lifetime_value=len(files), recent_value=len(files),
            trend_percent=None,
        ),
        "source_loc_kloc": OverviewCell(
            lifetime_value=kloc, recent_value=kloc, trend_percent=None,
        ),
        "commit_count": OverviewCell(
            lifetime_value=len(commits_in_scope),
            recent_value=len(recent_in_scope),
            trend_percent=None,
        ),
        "recent_commit_count": OverviewCell(
            lifetime_value=len(recent_in_scope),
            recent_value=len(recent_in_scope),
            trend_percent=None,
        ),
        "wide_commit_pct": OverviewCell(
            lifetime_value=wide_pct, recent_value=wide_pct, trend_percent=None,
        ),
        "deep_commit_pct": OverviewCell(
            lifetime_value=deep_pct, recent_value=deep_pct, trend_percent=None,
        ),
        "high_impact_task_count": OverviewCell(
            lifetime_value=high_impact, recent_value=high_impact,
            trend_percent=None,
        ),
        "scattered_task_count": OverviewCell(
            lifetime_value=scattered, recent_value=scattered, trend_percent=None,
        ),
    }
    return OverviewRow(entity_id=entity_id, cells=cells)


# ----------------------------------------------------------------------
# Indexes
# ----------------------------------------------------------------------
def _index_commits(
    graph: Any, component_of_file: dict[str, str],
    wide_min: int, deep_min: int, cutoff: Optional[Any],
) -> list[dict[str, Any]]:
    """One dict per commit: ``components`` touched, ``wide``, ``deep``,
    ``in_recent``. Computed once so the per-row scope filter is O(N).
    """
    commits_reg = getattr(graph, "commits", None)
    if commits_reg is None:
        return []
    try:
        commits = list(commits_reg)
    except TypeError:
        return []

    changes_by_commit = _changes_by_commit_index(graph)
    hunks_by_change = _hunks_by_change_index(graph)

    out: list[dict[str, Any]] = []
    for commit in commits:
        files_touched: set[str] = set()
        churn = 0
        for change in changes_by_commit(commit.ref()):
            fref = getattr(change, "file_ref", None)
            if fref is not None:
                files_touched.add(fref.id)
            for hunk in hunks_by_change(change.ref()):
                churn += len(getattr(hunk, "added_lines", []) or [])
                churn += len(getattr(hunk, "deleted_lines", []) or [])
        components = frozenset(
            component_of_file[fid]
            for fid in files_touched
            if fid in component_of_file
        )
        out.append({
            "id": commit.id,
            "components": components,
            "wide": len(files_touched) >= wide_min,
            "deep": churn >= deep_min,
            "in_recent": cutoff is not None and _commit_in_window(commit, cutoff),
        })
    return out


def _index_issues(
    graph: Any, component_of_file: dict[str, str],
    hi_min: int, scat_min: int,
) -> dict[str, Any]:
    """Per-issue file-touch set + ``high_impact`` / ``scattered`` rosters.

    Walks ``relations.of_kind("issue_file")`` once. The legacy form used
    a back-pointer (``issue.git_commits``) and counted files touched
    through commits; v2 reads the issue↔file edges directly, which is a
    more direct signal of "issue touches file" and avoids the back-pointer.
    """
    relations = getattr(graph, "relations", None)
    files_by_issue: dict[EntityRef, set[str]] = defaultdict(set)
    if relations is not None:
        of_kind = getattr(relations, "of_kind", None)
        if of_kind is not None:
            for rel in of_kind("issue_file"):
                src = getattr(rel, "source", None)
                tgt = getattr(rel, "target", None)
                if (
                    src is None or tgt is None
                    or src.kind != EntityKind.ISSUE
                    or tgt.kind != EntityKind.FILE
                ):
                    continue
                files_by_issue[src].add(tgt.id)

    high_impact: list[EntityRef] = []
    scattered: list[EntityRef] = []
    components_by_issue: dict[EntityRef, frozenset[str]] = {}
    for issue_ref, fids in files_by_issue.items():
        components = frozenset(
            component_of_file[fid]
            for fid in fids
            if fid in component_of_file
        )
        components_by_issue[issue_ref] = components
        if len(fids) >= hi_min:
            high_impact.append(issue_ref)
        if len(components) >= scat_min:
            scattered.append(issue_ref)

    return {
        "high_impact": high_impact,
        "scattered": scattered,
        "components_by_issue": components_by_issue,
    }


def _component_of_file(graph: Any) -> dict[str, str]:
    relations = getattr(graph, "relations", None)
    if relations is None:
        return {}
    of_kind = getattr(relations, "of_kind", None)
    if of_kind is None:
        return {}
    out: dict[str, str] = {}
    for rel in of_kind("component_membership"):
        src = getattr(rel, "source", None)
        tgt = getattr(rel, "target", None)
        if (
            src is None or tgt is None
            or src.kind != EntityKind.FILE
            or tgt.kind != EntityKind.COMPONENT
        ):
            continue
        out.setdefault(src.id, tgt.id)
    return out


def _ordered_component_names(
    graph: Any, component_of_file: dict[str, str],
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    components = getattr(graph, "components", None)
    if components is not None:
        try:
            for comp in components:
                name = getattr(comp, "name", None) or getattr(comp, "id", None)
                if not name or name in seen:
                    continue
                seen.add(name)
                out.append(name)
        except TypeError:
            pass
    extras = sorted(set(component_of_file.values()) - seen)
    out.extend(extras)
    return out


def _nloc_index(graph: Any) -> dict[str, float]:
    file_metrics = getattr(graph, "file_metrics", None)
    if file_metrics is None:
        return {}
    try:
        rows = list(file_metrics)
    except TypeError:
        return {}
    out: dict[str, float] = {}
    for row in rows:
        if getattr(row, "metric_name", None) != "sum_nloc":
            continue
        fref = getattr(row, "file_ref", None)
        value = getattr(row, "value", None)
        if fref is None or value is None:
            continue
        out[fref.id] = float(value)
    return out


# ----------------------------------------------------------------------
# Misc helpers
# ----------------------------------------------------------------------
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


def _cfg_int(config: Any, field: str) -> int:
    default = _DEFAULTS[field]
    if config is None:
        return default
    value = getattr(config, field, None)
    if value is None:
        return default
    return int(value)


def _percent(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 2)


__all__ = ["FeatureEncapsulationTableBuilder", "COLUMNS"]
