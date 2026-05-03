"""anomaly.cohesion.* — Bazaar, Cathedral, Pulsar (coordination) + Supernova (size).

Phase-2 originals plus the A2.1 cohesion file traits:
  - Hibernator (activity): lifetime-rich file with zero recent activity.
  - Awakening (activity): dormant file recently reactivated.
  - Erosion (activity): linearly declining commit cadence over its lifetime.
  - Flicker (coordination): high CV of inter-commit gaps in the recent window
    only — distinct from Pulsar which evaluates the lifetime distribution.
  - FrequentChanger (size): lifetime or recent commit count above thresholds.
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Iterable, Optional

from src.enrichment.models import EntityTags, Trait
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext, make_trait
from src.enrichment.tagger.file_classifiers import _file_id
from src.enrichment.tagger.file_trait_utils import (
    _commit_dates,
    _linear_slope,
    _time_bucketed_commits,
)


class CohesionAnomalyTagger:

    TRAITS = [
        {"name": "anomaly.cohesion.coordination.Bazaar",     "entity": "file", "family": "cohesion"},
        {"name": "anomaly.cohesion.coordination.Cathedral",  "entity": "file", "family": "cohesion"},
        {"name": "anomaly.cohesion.coordination.Pulsar",     "entity": "file", "family": "cohesion"},
        {"name": "anomaly.cohesion.coordination.Flicker",    "entity": "file", "family": "cohesion"},
        {"name": "anomaly.cohesion.size.Supernova",          "entity": "file", "family": "cohesion"},
        {"name": "anomaly.cohesion.size.FrequentChanger",    "entity": "file", "family": "cohesion"},
        {"name": "anomaly.cohesion.activity.Hibernator",     "entity": "file", "family": "cohesion"},
        {"name": "anomaly.cohesion.activity.Awakening",      "entity": "file", "family": "cohesion"},
        {"name": "anomaly.cohesion.activity.Erosion",        "entity": "file", "family": "cohesion"},
    ]

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

            # Supernova (proxy): net-churn over lifetime above threshold.
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

            # ── A2.1 cohesion traits ─────────────────────────────────────────

            dates = _commit_dates(file_)
            lifetime_commits = len(dates)
            recent_commit_dates = (
                [d for d in dates if cutoff is None or d >= cutoff]
                if cutoff else dates
            )

            # Hibernator: enough lifetime commits, zero recent commits.
            if (
                cutoff is not None
                and lifetime_commits >= cfg.hibernator_min_lifetime_commits
                and not recent_commit_dates
            ):
                last = max(dates) if dates else None
                traits.append(make_trait(
                    "anomaly.cohesion.activity.Hibernator",
                    family="cohesion",
                    lifetime_commits=lifetime_commits,
                    last_change=last.isoformat() if last else None,
                    threshold=cfg.hibernator_min_lifetime_commits,
                ))

            # Awakening: long pre-window dormancy then recent revival.
            if cutoff is not None and len(recent_commit_dates) >= cfg.awakening_recent_commits_min:
                pre_window = [d for d in dates if d < cutoff]
                if pre_window:
                    last_before = max(pre_window)
                    dormant = cutoff - last_before
                    if dormant >= timedelta(weeks=cfg.awakening_min_dormant_weeks):
                        traits.append(make_trait(
                            "anomaly.cohesion.activity.Awakening",
                            family="cohesion",
                            severity=float(dormant.days),
                            dormant_days=dormant.days,
                            recent_commits=len(recent_commit_dates),
                            last_pre_window_change=last_before.isoformat(),
                            threshold_weeks=cfg.awakening_min_dormant_weeks,
                        ))

            # Erosion: negative linear trend on per-window commit counts.
            buckets = _time_bucketed_commits(file_, cfg.erosion_window_weeks, fill_gaps=True)
            if len(buckets) >= 4:
                values = [float(c) for _, c in buckets]
                slope = _linear_slope(values)
                if slope is not None and slope <= cfg.erosion_trend_max:
                    traits.append(make_trait(
                        "anomaly.cohesion.activity.Erosion",
                        family="cohesion",
                        severity=round(-slope, 3),
                        slope=round(slope, 4),
                        bucket_count=len(buckets),
                        bucket_weeks=cfg.erosion_window_weeks,
                        threshold=cfg.erosion_trend_max,
                    ))

            # Flicker: recent-window CV of inter-commit gaps (Pulsar's
            # short-horizon counterpart).
            if len(recent_commit_dates) >= cfg.flicker_min_recent_commits:
                gaps = _gaps(sorted(recent_commit_dates))
                if len(gaps) >= 3:
                    cv_recent = _coeff_of_variation(gaps)
                    if cv_recent is not None and cv_recent >= cfg.flicker_cv_min:
                        traits.append(make_trait(
                            "anomaly.cohesion.coordination.Flicker",
                            family="cohesion",
                            severity=round(cv_recent, 3),
                            recent_interval_cv=round(cv_recent, 3),
                            recent_commits=len(recent_commit_dates),
                            recent_gaps=len(gaps),
                            threshold=cfg.flicker_cv_min,
                        ))

            # FrequentChanger: lifetime OR recent commit volume above thresholds.
            recent_total_commits = len(recent_commit_dates)
            if (
                lifetime_commits >= cfg.frequent_changer_lifetime_min
                or recent_total_commits >= cfg.frequent_changer_recent_min
            ):
                basis = (
                    "lifetime"
                    if lifetime_commits >= cfg.frequent_changer_lifetime_min
                    else "recent"
                )
                traits.append(make_trait(
                    "anomaly.cohesion.size.FrequentChanger",
                    family="cohesion",
                    severity=float(lifetime_commits),
                    basis=basis,
                    lifetime_commits=lifetime_commits,
                    recent_commits=recent_total_commits,
                    lifetime_threshold=cfg.frequent_changer_lifetime_min,
                    recent_threshold=cfg.frequent_changer_recent_min,
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


def _gaps(sorted_dates) -> list[float]:
    return [
        (sorted_dates[i + 1] - sorted_dates[i]).total_seconds()
        for i in range(len(sorted_dates) - 1)
    ]


def _coeff_of_variation(values: list[float]) -> Optional[float]:
    if not values:
        return None
    mean = sum(values) / len(values)
    if mean <= 0:
        return None
    var = sum((g - mean) ** 2 for g in values) / len(values)
    return math.sqrt(var) / mean


def _inter_commit_cv(file_, min_intervals: int) -> Optional[float]:
    dates = _commit_dates(file_)
    if len(dates) < min_intervals + 1:
        return None
    dates.sort()
    gaps = _gaps(dates)
    return _coeff_of_variation(gaps) if gaps else None
