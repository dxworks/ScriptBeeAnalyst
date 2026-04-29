"""anomaly.cohesion.* — Bazaar, Cathedral, Pulsar (coordination) + Supernova (size).

  - Bazaar: many distinct authors in the recent window.
  - Cathedral: one author dominates the recent window (counterpart).
  - Pulsar: bursty inter-commit interval distribution (lifetime CV >= threshold).
  - Supernova (Phase 3, proxy): net-churn lifetime above a threshold; flagged
    in `evidence` because we don't ingest absolute LOC.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

from src.enrichment.models import EntityTags, Trait
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext, make_trait
from src.enrichment.tagger.file_classifiers import _file_id


class CohesionAnomalyTagger:

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

            traits: list[Trait] = []

            recent_changes = _recent_changes(file_, cutoff)
            recent_authors = _author_counts(recent_changes)
            total_recent = sum(recent_authors.values())

            # Bazaar: distinct authors in recent window above threshold.
            if len(recent_authors) >= cfg.bazaar_distinct_authors_min:
                traits.append(make_trait(
                    "anomaly.cohesion.coordination.Bazaar",
                    family="cohesion",
                    severity=float(len(recent_authors)),
                    distinct_authors_recent=len(recent_authors),
                    threshold=cfg.bazaar_distinct_authors_min,
                ))

            # Cathedral: one author dominates the recent window.
            if total_recent >= cfg.cathedral_min_recent_commits and recent_authors:
                top_author, top_count = max(recent_authors.items(), key=lambda kv: kv[1])
                dominance = top_count / total_recent
                if dominance >= cfg.cathedral_dominance_ratio:
                    traits.append(make_trait(
                        "anomaly.cohesion.coordination.Cathedral",
                        family="cohesion",
                        severity=round(dominance, 3),
                        dominant_author=top_author,
                        dominance_ratio=round(dominance, 3),
                        threshold=cfg.cathedral_dominance_ratio,
                        recent_commits=total_recent,
                    ))

            # Pulsar: bursty commit timing — high CV of inter-commit gaps.
            cv = _inter_commit_cv(file_, cfg.pulsar_min_intervals)
            commits_count = sum(1 for ch in (file_.changes or []) if getattr(ch, "commit", None))
            if cv is not None and cv >= cfg.pulsar_cv_min and commits_count >= cfg.pulsar_min_commits:
                traits.append(make_trait(
                    "anomaly.cohesion.coordination.Pulsar",
                    family="cohesion",
                    severity=round(cv, 3),
                    interval_cv=round(cv, 3),
                    threshold=cfg.pulsar_cv_min,
                    commits=commits_count,
                ))

            # Supernova (proxy): net-churn over lifetime above threshold. We
            # cannot measure absolute LOC; sustained churn is the best stand-in.
            net_churn = _net_churn(file_)
            if net_churn >= cfg.supernova_net_churn_min:
                traits.append(make_trait(
                    "anomaly.cohesion.size.Supernova",
                    family="cohesion",
                    severity=float(net_churn),
                    proxy=True,
                    note="net-churn proxy, not absolute LOC",
                    net_churn=int(net_churn),
                    threshold=cfg.supernova_net_churn_min,
                ))

            if traits:
                out.append(EntityTags(
                    entity_kind="file",
                    entity_id=fid,
                    traits=traits,
                ))

        return out


def _recent_changes(file_, cutoff) -> list:
    out = []
    for ch in file_.changes or []:
        c = getattr(ch, "commit", None)
        if c is None:
            continue
        d = ensure_aware(getattr(c, "author_date", None))
        if d is None:
            continue
        if cutoff is None or d >= cutoff:
            out.append(ch)
    return out


def _author_counts(changes: Iterable) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ch in changes:
        c = getattr(ch, "commit", None)
        if c is None:
            continue
        a = getattr(c, "author", None)
        if a is None:
            continue
        aid = getattr(a, "id", None) or str(a)
        counts[aid] = counts.get(aid, 0) + 1
    return counts


def _net_churn(file_) -> int:
    total = 0
    for ch in file_.changes or []:
        for h in getattr(ch, "hunks", None) or []:
            total += len(getattr(h, "added_lines", []) or [])
            total += len(getattr(h, "deleted_lines", []) or [])
    return total


def _inter_commit_cv(file_, min_intervals: int) -> Optional[float]:
    dates = []
    for ch in file_.changes or []:
        c = getattr(ch, "commit", None)
        if c is None:
            continue
        d = ensure_aware(getattr(c, "author_date", None))
        if d is not None:
            dates.append(d)
    # Need at least `min_intervals` gaps, i.e. min_intervals + 1 timestamps.
    if len(dates) < min_intervals + 1:
        return None
    dates.sort()
    gaps = [(dates[i + 1] - dates[i]).total_seconds() for i in range(len(dates) - 1)]
    if not gaps:
        return None
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return None
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    sd = math.sqrt(var)
    return sd / mean
