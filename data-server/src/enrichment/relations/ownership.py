"""Author ↔ File ownership — port of dx's author-file churn graph.

Edge strength = author churn on file / total file churn (relative ownership).
Absolute churn is preserved in `extras['absolute_churn']` for callers that need it.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from src.enrichment.models import Relation, RelationFile
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


class OwnershipExtractor:
    """Emits two RelationFiles: lifetime + recent."""

    KIND = "ownership.author-file"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        cutoff = ctx.recent_cutoff

        lifetime: dict[tuple[str, str], int] = defaultdict(int)
        recent: dict[tuple[str, str], int] = defaultdict(int)
        lifetime_totals: dict[str, int] = defaultdict(int)
        recent_totals: dict[str, int] = defaultdict(int)

        for file_ in git.file_registry.all:
            fid = _file_id(file_)
            if fid is None:
                continue
            for change in file_.changes or []:
                c = getattr(change, "commit", None)
                if c is None:
                    continue
                a = getattr(c, "author", None)
                if a is None:
                    continue
                aid = getattr(a, "id", None) or str(a)
                amount = _change_churn(change)
                lifetime[(aid, fid)] += amount
                lifetime_totals[fid] += amount
                d = ensure_aware(getattr(c, "author_date", None))
                if cutoff is not None and d is not None and d >= cutoff:
                    recent[(aid, fid)] += amount
                    recent_totals[fid] += amount

        return [
            _to_relation_file(self.KIND, "lifetime", lifetime, lifetime_totals),
            _to_relation_file(self.KIND, "recent", recent, recent_totals),
        ]


def _change_churn(change) -> int:
    total = 0
    for hunk in getattr(change, "hunks", None) or []:
        total += len(getattr(hunk, "added_lines", []) or [])
        total += len(getattr(hunk, "deleted_lines", []) or [])
    return total if total > 0 else 1  # at least record the touch


def _to_relation_file(kind, window, pairs, totals) -> RelationFile:
    relations = []
    for (aid, fid), churn in pairs.items():
        total = totals.get(fid, 0)
        strength = (churn / total) if total > 0 else 0.0
        relations.append(Relation(
            source_kind="author",
            source_id=aid,
            target_kind="file",
            target_id=fid,
            kind=kind,
            strength=round(strength, 6),
            extras={"absolute_churn": int(churn), "file_total_churn": int(total)},
        ))
    relations.sort(key=lambda r: -r.strength)
    return RelationFile(kind=kind, window=window, relations=relations)
