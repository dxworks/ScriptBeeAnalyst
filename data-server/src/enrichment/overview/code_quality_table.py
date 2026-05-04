"""Code quality overview table — per-component code-smell rollup from Insider.

Implements §7.2 of communication/B4_sonar_insider/index_step_general.md, with
the data-format corrections from index_step_data_format.md (occurrence counts,
not severities).

Columns (lifetime-only; per-file occurrence counts have no recent-window
dimension because Insider does not timestamp its findings):
  - total_smells      — sum of `occurrence_count` across the component's files
  - distinct_rules    — count of distinct rule_names that fired in the component
  - distinct_files    — count of files in the component carrying ANY code smell
  - top_rule          — name of the rule with the highest aggregate occurrence_count
  - top_rule_count    — that rule's aggregate occurrence_count
  - top_rule_2        — runner-up rule name (None when fewer than 2 rules fired)
  - top_rule_2_count  — runner-up aggregate occurrence_count

Rows: synthetic '(project)' aggregate + one per resolved component (mirrors
ComponentsTableBuilder).
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
from src.enrichment.tagger.base import TaggingContext


COLUMNS = [
    "total_smells",
    "distinct_rules",
    "distinct_files",
    "top_rule",
    "top_rule_count",
    "top_rule_2",
    "top_rule_2_count",
]


class CodeQualityOverview:
    """Per-component code-smell rollup (B4)."""

    NAME = "code_quality"
    ENTITY_KIND = "component"

    def build(
        self,
        ctx: TaggingContext,
        tags_by_entity: dict,
        components: list[Component],
        resolver: ComponentResolver,
    ) -> OverviewTable:
        rows: list[OverviewRow] = []
        qi = ctx.graph_data.get("quality_issues")
        if qi is None or not qi.issues:
            return OverviewTable(
                name=self.NAME, entity_kind="component",
                columns=COLUMNS, rows=rows,
            )

        # Bucket issues by component name (None = unresolved).
        issues_by_component: dict[str, list] = defaultdict(list)
        all_issues = list(qi.issues)
        for issue in all_issues:
            comp = resolver.resolve(issue.file_path)
            if comp is None:
                continue
            issues_by_component[comp].append(issue)

        rows.append(self._row_for("(project)", all_issues))
        for c in components:
            rows.append(self._row_for(c.name, issues_by_component.get(c.name, [])))

        return OverviewTable(
            name=self.NAME, entity_kind="component",
            columns=COLUMNS, rows=rows,
        )

    def _row_for(self, entity_id: str, issues: list) -> OverviewRow:
        cells: dict[str, OverviewCell] = {}
        if not issues:
            for col in COLUMNS:
                cells[col] = OverviewCell(
                    lifetime_value=None if col.startswith("top_rule") else 0,
                    recent_value=None if col.startswith("top_rule") else 0,
                    trend_percent=None,
                )
            return OverviewRow(entity_id=entity_id, cells=cells)

        total = sum(i.occurrence_count for i in issues)
        rules = {i.rule_name for i in issues}
        files = {i.file_path for i in issues}

        # Top-N rules by aggregate occurrence_count.
        per_rule: dict[str, int] = defaultdict(int)
        for i in issues:
            per_rule[i.rule_name] += i.occurrence_count
        ranked = sorted(per_rule.items(), key=lambda kv: -kv[1])
        top_rule, top_rule_count = ranked[0]
        top_rule_2, top_rule_2_count = (ranked[1] if len(ranked) > 1 else (None, None))

        cells["total_smells"] = _scalar_cell(total)
        cells["distinct_rules"] = _scalar_cell(len(rules))
        cells["distinct_files"] = _scalar_cell(len(files))
        cells["top_rule"] = _scalar_cell(top_rule)
        cells["top_rule_count"] = _scalar_cell(top_rule_count)
        cells["top_rule_2"] = _scalar_cell(top_rule_2)
        cells["top_rule_2_count"] = _scalar_cell(top_rule_2_count)

        return OverviewRow(entity_id=entity_id, cells=cells)


def _scalar_cell(value) -> OverviewCell:
    return OverviewCell(
        lifetime_value=value,
        recent_value=value,
        trend_percent=None,
    )
