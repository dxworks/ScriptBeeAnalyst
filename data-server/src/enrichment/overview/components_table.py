"""Components overview — per folder-component rollup.

Columns (lifetime + recent + trend% on rate-like columns):
  - file_count
  - total_churn          — sum of added+deleted across the component's files
  - commit_count         — distinct commits touching any file in the component
  - distinct_authors
  - bugfix_ratio         — bugfix commits / commit_count
  - bus_factor_1_files   — files in the component carrying BusFactor1
  - bugmagnet_files      — files in the component carrying BugMagnet
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
from src.enrichment.recent_window import ensure_aware, trend_percent
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


COLUMNS = [
    "file_count",
    "total_churn",
    "commit_count",
    "distinct_authors",
    "bugfix_ratio",
    "bus_factor_1_files",
    "bugmagnet_files",
]


class ComponentsTableBuilder:

    NAME = "components"

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
            return OverviewTable(name=self.NAME, entity_kind="component", columns=COLUMNS, rows=rows)

        cutoff = ctx.recent_cutoff

        files_by_component: dict[str, list] = defaultdict(list)
        for f in git.file_registry.all:
            fid = _file_id(f)
            if not fid:
                continue
            comp = resolver.resolve(fid)
            if comp is None:
                continue
            files_by_component[comp].append(f)

        all_files = list(git.file_registry.all)

        rows.append(self._row_for("(project)", all_files, cutoff, tags_by_entity))

        ordered_names = [c.name for c in components]
        for name in ordered_names:
            rows.append(self._row_for(
                name,
                files_by_component.get(name, []),
                cutoff,
                tags_by_entity,
            ))

        return OverviewTable(
            name=self.NAME,
            entity_kind="component",
            columns=COLUMNS,
            rows=rows,
        )

    def _row_for(self, entity_id, files, cutoff, tags_by_entity) -> OverviewRow:
        lifetime_churn = 0
        recent_churn = 0
        lifetime_commits: set[str] = set()
        recent_commits: set[str] = set()
        lifetime_authors: set[str] = set()
        recent_authors: set[str] = set()
        lifetime_bugfix_commits: set[str] = set()
        recent_bugfix_commits: set[str] = set()
        bf1 = 0
        bm = 0

        for f in files:
            fid = _file_id(f)
            if fid is None:
                continue
            ftags = tags_by_entity.get(f"file:{fid}")
            if ftags:
                if any(t.name == "anomaly.knowledge.BusFactor1" for t in ftags.traits):
                    bf1 += 1
                if any(t.name == "anomaly.testing.BugMagnet" for t in ftags.traits):
                    bm += 1
            for ch in f.changes or []:
                c = getattr(ch, "commit", None)
                if c is None:
                    continue
                cid = getattr(c, "id", None)
                a = getattr(c, "author", None)
                aid = getattr(a, "id", None) if a is not None else None
                churn = _change_churn(ch)
                lifetime_churn += churn
                if cid:
                    lifetime_commits.add(cid)
                if aid:
                    lifetime_authors.add(aid)
                d = ensure_aware(getattr(c, "author_date", None))
                in_recent = cutoff is not None and d is not None and d >= cutoff
                if in_recent:
                    recent_churn += churn
                    if cid:
                        recent_commits.add(cid)
                    if aid:
                        recent_authors.add(aid)
                ctags = tags_by_entity.get(f"commit:{cid}") if cid else None
                if ctags and ctags.classifiers.get("message.nature") == "bugfix":
                    if cid:
                        lifetime_bugfix_commits.add(cid)
                        if in_recent:
                            recent_bugfix_commits.add(cid)

        cells: dict[str, OverviewCell] = {}

        cells["file_count"] = OverviewCell(
            lifetime_value=len(files),
            recent_value=len(files),
            trend_percent=None,
        )
        cells["total_churn"] = _rate_cell(lifetime_churn, recent_churn)
        cells["commit_count"] = _rate_cell(len(lifetime_commits), len(recent_commits))
        cells["distinct_authors"] = OverviewCell(
            lifetime_value=len(lifetime_authors),
            recent_value=len(recent_authors),
            trend_percent=None,
        )

        lt_ratio = _safe_ratio(len(lifetime_bugfix_commits), len(lifetime_commits))
        rc_ratio = _safe_ratio(len(recent_bugfix_commits), len(recent_commits))
        cells["bugfix_ratio"] = OverviewCell(
            lifetime_value=lt_ratio,
            recent_value=rc_ratio,
            trend_percent=trend_percent(lt_ratio, rc_ratio),
        )

        cells["bus_factor_1_files"] = OverviewCell(
            lifetime_value=bf1, recent_value=bf1, trend_percent=None,
        )
        cells["bugmagnet_files"] = OverviewCell(
            lifetime_value=bm, recent_value=bm, trend_percent=None,
        )

        return OverviewRow(entity_id=entity_id, cells=cells)


# ── helpers ────────────────────────────────────────────────────────────────────

def _change_churn(change) -> int:
    total = 0
    for hunk in getattr(change, "hunks", None) or []:
        total += len(getattr(hunk, "added_lines", []) or [])
        total += len(getattr(hunk, "deleted_lines", []) or [])
    return total if total > 0 else 1


def _safe_ratio(n: int, d: int) -> Optional[float]:
    if d <= 0:
        return None
    return round(n / d, 4)


def _rate_cell(lt: int, rc: int) -> OverviewCell:
    lt_v = lt if lt else 0
    rc_v = rc if rc else 0
    return OverviewCell(
        lifetime_value=lt_v,
        recent_value=rc_v,
        trend_percent=trend_percent(lt_v or None, rc_v or None),
    )
