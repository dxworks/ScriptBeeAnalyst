"""anomaly.knowledge.* — Orphan, BusFactor1 (Hermit), SharedKnowledge.

All three are file-level traits derived from author-churn distribution:
  - Orphan: only ever one author touched it, last touch outside the recent window.
  - BusFactor1 / Hermit: one author owns >= hermit_dominance_ratio of churn.
  - SharedKnowledge: entropy of author-churn distribution >= threshold.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

from src.enrichment.models import EntityTags, Trait
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext, make_trait
from src.enrichment.tagger.file_classifiers import _file_id


class KnowledgeAnomalyTagger:
    """Emits Orphan, BusFactor1, SharedKnowledge file traits."""

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        cfg = ctx.config
        cutoff = ctx.recent_cutoff
        out: list[EntityTags] = []

        for file_ in git.file_registry.all:
            fid = _file_id(file_)
            if fid is None:
                continue

            churn_by_author = _author_churn(file_)
            if not churn_by_author:
                continue

            traits: list[Trait] = []
            total = sum(churn_by_author.values())

            last = _last_change_date(file_)

            # Orphan: single author ever AND last touch outside recent window.
            if len(churn_by_author) == 1:
                outside_recent = (
                    cutoff is not None and last is not None and last < cutoff
                )
                if outside_recent:
                    only_author = next(iter(churn_by_author.keys()))
                    traits.append(make_trait(
                        "anomaly.knowledge.Orphan",
                        family="knowledge",
                        author=only_author,
                        last_change=last.isoformat() if last else None,
                        churn=int(total),
                    ))

            # BusFactor1 / Hermit: dominant author > threshold.
            top_author, top_churn = max(churn_by_author.items(), key=lambda kv: kv[1])
            dominance = top_churn / total if total > 0 else 0.0
            if (
                dominance >= cfg.hermit_dominance_ratio
                and len(churn_by_author) >= cfg.busfactor1_min_distinct_authors
            ):
                traits.append(make_trait(
                    "anomaly.knowledge.BusFactor1",
                    family="knowledge",
                    severity=round(dominance, 3),
                    dominant_author=top_author,
                    dominance_ratio=round(dominance, 3),
                    threshold=cfg.hermit_dominance_ratio,
                    distinct_authors=len(churn_by_author),
                ))

            # SharedKnowledge: high entropy of churn distribution (counterpart).
            entropy = _entropy(churn_by_author.values(), total)
            if (
                entropy >= cfg.shared_knowledge_entropy_min
                and len(churn_by_author) >= cfg.shared_knowledge_min_distinct_authors
            ):
                traits.append(make_trait(
                    "anomaly.knowledge.SharedKnowledge",
                    family="knowledge",
                    severity=round(entropy, 3),
                    entropy=round(entropy, 3),
                    threshold=cfg.shared_knowledge_entropy_min,
                    distinct_authors=len(churn_by_author),
                ))

            if traits:
                out.append(EntityTags(
                    entity_kind="file",
                    entity_id=fid,
                    traits=traits,
                ))

        return out


def _author_churn(file_) -> dict[str, int]:
    churn: dict[str, int] = {}
    for change in file_.changes or []:
        commit = getattr(change, "commit", None)
        if commit is None:
            continue
        author = getattr(commit, "author", None)
        if author is None:
            continue
        author_id = getattr(author, "id", None) or str(author)
        added = sum(len(getattr(h, "added_lines", []) or []) for h in (getattr(change, "hunks", None) or []))
        deleted = sum(len(getattr(h, "deleted_lines", []) or []) for h in (getattr(change, "hunks", None) or []))
        amount = added + deleted
        if amount == 0:
            amount = 1  # at least record the touch
        churn[author_id] = churn.get(author_id, 0) + amount
    return churn


def _last_change_date(file_):
    dates = [ensure_aware(getattr(ch.commit, "author_date", None))
             for ch in (file_.changes or [])
             if getattr(ch, "commit", None) is not None]
    dates = [d for d in dates if d is not None]
    return max(dates) if dates else None


def _entropy(values: Iterable[int], total: int) -> float:
    if total <= 0:
        return 0.0
    h = 0.0
    for v in values:
        if v <= 0:
            continue
        p = v / total
        h -= p * math.log(p)
    return h
