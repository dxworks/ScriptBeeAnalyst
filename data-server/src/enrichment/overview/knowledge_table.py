"""Knowledge overview table.

Splits the knowledge concerns out of Authorship into a standalone table — dx
exposes APK% and ownership-anomaly counts as their own dashboard pane.

Columns (lifetime + recent + trend% on share-like cells):
  - apk_percent              — Active Programmers' Knowledge: % of churn owned
                               by authors classified `activity=active`.
  - weak_ownership_count     — files in scope tagged anomaly.knowledge.WeakOwnership.
  - polarised_ownership_count — files tagged anomaly.knowledge.PolarisedOwnership.
  - orphan_count             — files tagged anomaly.knowledge.Orphan.
  - newcomer_ratio           — share of authors-in-scope classified seniority=newcomer.

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
    "apk_percent",
    "weak_ownership_count",
    "polarised_ownership_count",
    "orphan_count",
    "newcomer_ratio",
]


# Ownership-anomaly trait names — counted as raw lifetime totals (no recent
# delta because the underlying tagger fires once per file, not per-window).
_OWNERSHIP_TRAITS = {
    "weak_ownership_count":      "anomaly.knowledge.WeakOwnership",
    "polarised_ownership_count": "anomaly.knowledge.PolarisedOwnership",
    "orphan_count":              "anomaly.knowledge.Orphan",
}


class KnowledgeTableBuilder:

    NAME = "knowledge"
    ENTITY_KIND = "component"

    def build(self, ctx: TaggingContext, tags_by_entity: dict) -> OverviewTable:
        git = ctx.graph_data.get("git")
        rows: list[OverviewRow] = []
        if git is None:
            return OverviewTable(name=self.NAME, entity_kind="component", columns=COLUMNS, rows=rows)

        cutoff = ctx.recent_cutoff
        all_files = list(git.file_registry.all)
        files_by_folder = _files_by_top_folder(all_files)

        rows.append(self._row_for("(project)", all_files, cutoff, tags_by_entity))
        for folder, files in sorted(files_by_folder.items()):
            rows.append(self._row_for(folder, files, cutoff, tags_by_entity))

        return OverviewTable(
            name=self.NAME,
            entity_kind="component",
            columns=COLUMNS,
            rows=rows,
        )

    def _row_for(self, entity_id, files, cutoff, tags_by_entity) -> OverviewRow:
        # Aggregate per-author churn (lifetime + recent) and per-trait counts in
        # a single pass over the scope's files.
        lifetime_churn_by_author: dict[str, int] = defaultdict(int)
        recent_churn_by_author: dict[str, int] = defaultdict(int)
        trait_counts: dict[str, int] = {k: 0 for k in _OWNERSHIP_TRAITS}

        for f in files:
            fid = _file_id(f)
            if fid is None:
                continue
            ftags = tags_by_entity.get(f"file:{fid}")
            if ftags:
                trait_names = {t.name for t in ftags.traits}
                for col, trait in _OWNERSHIP_TRAITS.items():
                    if trait in trait_names:
                        trait_counts[col] += 1
            for ch in f.changes or []:
                c = getattr(ch, "commit", None)
                if c is None:
                    continue
                a = getattr(c, "author", None)
                if a is None:
                    continue
                aid = getattr(a, "id", None) or str(a)
                churn = _change_churn(ch)
                lifetime_churn_by_author[aid] += churn
                d = ensure_aware(getattr(c, "author_date", None))
                if cutoff is not None and d is not None and d >= cutoff:
                    recent_churn_by_author[aid] += churn

        cells: dict[str, OverviewCell] = {}

        # APK% — share (in %) of churn coming from currently-active authors.
        lt_apk = _apk_percent(lifetime_churn_by_author, tags_by_entity)
        rc_apk = _apk_percent(recent_churn_by_author, tags_by_entity)
        cells["apk_percent"] = OverviewCell(
            lifetime_value=lt_apk,
            recent_value=rc_apk,
            trend_percent=trend_percent(lt_apk, rc_apk),
        )

        # Raw lifetime counts — no per-window split (mirrors how
        # bus_factor_1_files is exposed in the Authorship table).
        for col, count in trait_counts.items():
            cells[col] = OverviewCell(
                lifetime_value=count,
                recent_value=None,
                trend_percent=None,
            )

        lt_newcomer = _seniority_share(lifetime_churn_by_author.keys(), tags_by_entity, "newcomer")
        rc_newcomer = _seniority_share(recent_churn_by_author.keys(), tags_by_entity, "newcomer")
        cells["newcomer_ratio"] = OverviewCell(
            lifetime_value=lt_newcomer,
            recent_value=rc_newcomer,
            trend_percent=trend_percent(lt_newcomer, rc_newcomer),
        )

        return OverviewRow(entity_id=entity_id, cells=cells)


# ── helpers ────────────────────────────────────────────────────────────────────

def _files_by_top_folder(files) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for f in files:
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


def _apk_percent(churn_by_author: dict, tags_by_entity: dict) -> Optional[float]:
    total = sum(churn_by_author.values())
    if total <= 0:
        return None
    active = 0
    for aid, churn in churn_by_author.items():
        atags = tags_by_entity.get(f"author:{aid}")
        if atags and atags.classifiers.get("activity") == "active":
            active += churn
    return round(100.0 * active / total, 2)


def _seniority_share(author_ids, tags_by_entity, target: str) -> Optional[float]:
    ids = list(author_ids)
    if not ids:
        return None
    matched = 0
    for aid in ids:
        atags = tags_by_entity.get(f"author:{aid}")
        if atags and atags.classifiers.get("seniority") == target:
            matched += 1
    return round(matched / len(ids), 4)
