"""Authorship overview table.

Columns (lifetime + recent + trend% where applicable):
  - total_authors: distinct authors that ever touched code in scope
  - active_authors: authors with a commit in the recent window
  - newcomer_ratio: fraction of authors classified as 'newcomer'
  - senior_ratio: fraction classified as 'senior' or 'veteran'
  - bus_factor_1_files: count of files carrying anomaly.knowledge.BusFactor1
  - dominant_author_share: share of churn owned by the top-1 author in scope

Rows: synthetic '(project)' aggregate + one per top-level folder.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from src.enrichment.models import OverviewCell, OverviewRow, OverviewTable
from src.enrichment.recent_window import ensure_aware, trend_percent
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


COLUMNS = [
    "total_authors",
    "active_authors",
    "newcomer_ratio",
    "senior_ratio",
    "bus_factor_1_files",
    "dominant_author_share",
]


class AuthorshipTableBuilder:

    NAME = "authorship"
    ENTITY_KIND = "component"

    def build(self, ctx: TaggingContext, tags_by_entity: dict) -> OverviewTable:
        git = ctx.graph_data.get("git")
        rows: list[OverviewRow] = []
        if git is None:
            return OverviewTable(name=self.NAME, entity_kind="component", columns=COLUMNS, rows=rows)

        cutoff = ctx.recent_cutoff

        files_by_folder = _files_by_top_folder(git)
        all_files = list(git.file_registry.all)

        rows.append(self._row_for(
            "(project)", all_files, cutoff, tags_by_entity,
        ))
        for folder, files in sorted(files_by_folder.items()):
            rows.append(self._row_for(folder, files, cutoff, tags_by_entity))

        return OverviewTable(
            name=self.NAME,
            entity_kind="component",
            columns=COLUMNS,
            rows=rows,
        )

    def _row_for(self, entity_id, files, cutoff, tags_by_entity) -> OverviewRow:
        # Lifetime metrics
        lifetime_authors: dict[str, int] = defaultdict(int)
        recent_authors: dict[str, int] = defaultdict(int)
        bus_factor_files = 0

        for f in files:
            fid = _file_id(f)
            if fid is None:
                continue
            ftags = tags_by_entity.get(f"file:{fid}")
            if ftags and any(t.name == "anomaly.knowledge.BusFactor1" for t in ftags.traits):
                bus_factor_files += 1
            for ch in f.changes or []:
                c = getattr(ch, "commit", None)
                if c is None:
                    continue
                a = getattr(c, "author", None)
                if a is None:
                    continue
                aid = getattr(a, "id", None) or str(a)
                churn = _change_churn(ch)
                lifetime_authors[aid] += churn
                d = ensure_aware(getattr(c, "author_date", None))
                if cutoff is not None and d is not None and d >= cutoff:
                    recent_authors[aid] += churn

        cells: dict[str, OverviewCell] = {}

        cells["total_authors"] = OverviewCell(
            lifetime_value=len(lifetime_authors),
            recent_value=len(recent_authors),
            trend_percent=trend_percent(
                len(lifetime_authors) or None,
                len(recent_authors) or None,
            ),
        )
        cells["active_authors"] = OverviewCell(
            lifetime_value=len(lifetime_authors),
            recent_value=len(recent_authors),
            trend_percent=None,
        )

        cells["newcomer_ratio"] = OverviewCell(
            lifetime_value=_seniority_share(lifetime_authors.keys(), tags_by_entity, {"newcomer"}),
            recent_value=_seniority_share(recent_authors.keys(), tags_by_entity, {"newcomer"}),
            trend_percent=None,
        )
        cells["senior_ratio"] = OverviewCell(
            lifetime_value=_seniority_share(lifetime_authors.keys(), tags_by_entity, {"senior", "veteran"}),
            recent_value=_seniority_share(recent_authors.keys(), tags_by_entity, {"senior", "veteran"}),
            trend_percent=None,
        )

        cells["bus_factor_1_files"] = OverviewCell(
            lifetime_value=bus_factor_files,
            recent_value=bus_factor_files,
            trend_percent=None,
        )

        cells["dominant_author_share"] = OverviewCell(
            lifetime_value=_dominant_share(lifetime_authors),
            recent_value=_dominant_share(recent_authors),
            trend_percent=None,
        )

        return OverviewRow(entity_id=entity_id, cells=cells)


# ── helpers ────────────────────────────────────────────────────────────────────

def _files_by_top_folder(git) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for f in git.file_registry.all:
        fid = _file_id(f)
        if not fid:
            continue
        top = fid.split("/", 1)[0] if "/" in fid else fid
        out[top].append(f)
    return dict(out)


def _change_churn(change) -> int:
    total = 0
    for hunk in getattr(change, "hunks", None) or []:
        total += len(getattr(hunk, "added_lines", []) or [])
        total += len(getattr(hunk, "deleted_lines", []) or [])
    return total if total > 0 else 1


def _seniority_share(author_ids, tags_by_entity, target: set[str]) -> Optional[float]:
    ids = list(author_ids)
    if not ids:
        return None
    matched = 0
    for aid in ids:
        tags = tags_by_entity.get(f"author:{aid}")
        if tags and tags.classifiers.get("seniority") in target:
            matched += 1
    return round(matched / len(ids), 4)


def _dominant_share(churn_by_author: dict) -> Optional[float]:
    if not churn_by_author:
        return None
    total = sum(churn_by_author.values())
    if total <= 0:
        return None
    top = max(churn_by_author.values())
    return round(top / total, 4)
