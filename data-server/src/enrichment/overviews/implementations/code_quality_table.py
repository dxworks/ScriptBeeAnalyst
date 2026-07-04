"""Code-quality overview — v2 port (Chunk 17).

Port of legacy ``src/enrichment/overview/code_quality_table.py``. Per-folder
code-smell rollup from quality issues + Chunk-12 anomaly quality-issue
traits.

Reads:

* ``graph.quality_issues`` (and its ``by_file`` index) for the raw
  occurrence-count rollup.
* ``graph.traits`` (name prefix ``anomaly.codesmell.``) emitted by the
  Chunk-12 :class:`AnomalyQualityIssuesMetric` — every file carrying any
  ``anomaly.codesmell.*`` trait contributes one to the ``hotspot_files``
  column.

Columns (lifetime-only — Insider/Sonar findings carry no timestamp):

  total_smells, distinct_rules, distinct_files, top_rule, top_rule_count,
  top_rule_2, top_rule_2_count, hotspot_files.

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

if TYPE_CHECKING:
    from src.common.kernel import Graph


COLUMNS: list[str] = [
    "total_smells",
    "distinct_rules",
    "distinct_files",
    "top_rule",
    "top_rule_count",
    "top_rule_2",
    "top_rule_2_count",
    "hotspot_files",
]

_HOTSPOT_TRAIT_PREFIX = "anomaly.codesmell."


@OVERVIEWS.register
class CodeQualityTableBuilder(OverviewTableBuilder):
    """One row per top-level folder + a synthetic ``(project)`` aggregate."""

    name: ClassVar[str] = "code_quality"

    def build(self, graph: "Graph", config: Any) -> OverviewTable:
        quality_reg = getattr(graph, "quality_issues", None)
        if quality_reg is None:
            return OverviewTable(
                name=self.name, entity_kind="component",
                columns=COLUMNS, rows=[],
            )
        try:
            issues = list(quality_reg)
        except TypeError:
            issues = []

        hotspot_file_ids = _file_ids_with_trait_prefix(
            graph, _HOTSPOT_TRAIT_PREFIX,
        )

        issues_by_folder: dict[str, list[Any]] = defaultdict(list)
        hotspots_by_folder: dict[str, set[str]] = defaultdict(set)
        for issue in issues:
            folder = _folder_for_issue(issue)
            if folder is None:
                continue
            issues_by_folder[folder].append(issue)

        for fid in hotspot_file_ids:
            top = top_folder_of(fid)
            if top is None:
                continue
            hotspots_by_folder[top].add(fid)

        rows: list[OverviewRow] = [
            _row_for("(project)", issues, len(hotspot_file_ids))
        ]
        for folder in sorted(issues_by_folder.keys()):
            rows.append(
                _row_for(
                    folder,
                    issues_by_folder[folder],
                    len(hotspots_by_folder.get(folder, set())),
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
    issues: list[Any],
    hotspot_count: int,
) -> OverviewRow:
    cells: dict[str, OverviewCell] = {}
    if not issues:
        for col in COLUMNS:
            cells[col] = _empty_cell(col)
        # Even a zero-issue folder may still carry hotspot files when
        # the metric flagged them on a different dimension. Surface it.
        cells["hotspot_files"] = OverviewCell(
            lifetime_value=hotspot_count,
            recent_value=hotspot_count,
            trend_percent=None,
        )
        return OverviewRow(entity_id=entity_id, cells=cells)

    total = sum(getattr(i, "occurrence_count", 1) or 1 for i in issues)
    rules = {i.rule_id for i in issues}
    files = {i.file_ref.id for i in issues if getattr(i, "file_ref", None) is not None}

    per_rule: dict[str, int] = defaultdict(int)
    for i in issues:
        per_rule[i.rule_id] += getattr(i, "occurrence_count", 1) or 1
    ranked = sorted(per_rule.items(), key=lambda kv: (-kv[1], kv[0]))
    top_rule, top_rule_count = ranked[0]
    top_rule_2, top_rule_2_count = (
        ranked[1] if len(ranked) > 1 else (None, None)
    )

    cells["total_smells"] = _scalar_cell(total)
    cells["distinct_rules"] = _scalar_cell(len(rules))
    cells["distinct_files"] = _scalar_cell(len(files))
    cells["top_rule"] = _scalar_cell(top_rule)
    cells["top_rule_count"] = _scalar_cell(top_rule_count)
    cells["top_rule_2"] = _scalar_cell(top_rule_2)
    cells["top_rule_2_count"] = _scalar_cell(top_rule_2_count)
    cells["hotspot_files"] = _scalar_cell(hotspot_count)

    return OverviewRow(entity_id=entity_id, cells=cells)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _folder_for_issue(issue: Any) -> Optional[str]:
    fref = getattr(issue, "file_ref", None)
    if fref is None:
        return None
    return top_folder_of(fref.id)


def _file_ids_with_trait_prefix(graph: Any, prefix: str) -> set[str]:
    """Every file id carrying at least one trait whose ``name`` starts with
    ``prefix``.

    There is no prefix-keyed index on :class:`TraitRegistry`; a full
    scan is acceptable because the registry is small (one row per
    flagged file). We deliberately walk ``all()`` rather than the
    per-name index to avoid coupling this overview to a closed list of
    rule/category trait names.
    """
    traits = getattr(graph, "traits", None)
    if traits is None:
        return set()
    out: set[str] = set()
    try:
        rows = list(traits)
    except TypeError:
        rows = []
    for t in rows:
        if not t.name.startswith(prefix):
            continue
        target: EntityRef = t.target
        if target.kind != EntityKind.FILE:
            continue
        out.add(target.id)
    return out


def _scalar_cell(value: Any) -> OverviewCell:
    return OverviewCell(
        lifetime_value=value,
        recent_value=value,
        trend_percent=None,
    )


def _empty_cell(col: str) -> OverviewCell:
    """Empty cells for "no issues": strings → None, counts → 0."""
    is_string_col = col.startswith("top_rule") and not col.endswith("count")
    zero: Any = None if is_string_col else 0
    return OverviewCell(
        lifetime_value=zero,
        recent_value=zero,
        trend_percent=None,
    )


__all__ = ["CodeQualityTableBuilder", "COLUMNS"]
