"""Cohesion anomaly metric — v2 port (Chunks 15a + 15b).

Port of legacy ``src/enrichment/tagger/anomaly_cohesion.py``. Emits the
nine :attr:`TraitFamily.COHESION` traits across three sub-families:

* **coordination** — Bazaar, Cathedral, Pulsar, Flicker (Chunk 15a).
  Author-distribution / inter-commit-spacing signals on a single file.
* **size** — Supernova (*proxy*), FrequentChanger (Chunk 15a).
  Volume-based signals: net-churn and commit count.
* **activity** — Hibernator, Awakening, Erosion (Chunk 15b).
  Time-decay / reactivation / trend-fit signals on commit cadence.

Reads:

* ``graph.files``                            — file population.
* ``graph.changes.by_file``                  — per-file change walks.
* ``graph.commits.get``                      — commit metadata
                                              (author_date, author_ref).
* ``graph.hunks.by_change``                  — Supernova net-churn
                                              (via ``change_net_churn``).
* ``graph.recent_cutoff`` (optional)         — recent window anchor.

Consumes ``file_trait_utils.change_net_churn`` (Supernova),
``file_trait_utils.time_bucketed_commits`` + ``file_trait_utils.linear_slope``
(Erosion) from Chunk 11. ``frequent_changer_recent_*`` evaluates against
the recent cutoff which mirrors the legacy semantics.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Optional

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tags import Trait, TraitFamily
from src.enrichment.utils.file_trait_utils import (
    change_net_churn,
    linear_slope,
    time_bucketed_commits,
)

if TYPE_CHECKING:
    from src.common.kernel import Graph


# ----------------------------------------------------------------------
# Trait names
# ----------------------------------------------------------------------
_TRAIT_BAZAAR = "anomaly.cohesion.coordination.Bazaar"
_TRAIT_CATHEDRAL = "anomaly.cohesion.coordination.Cathedral"
_TRAIT_PULSAR = "anomaly.cohesion.coordination.Pulsar"
_TRAIT_FLICKER = "anomaly.cohesion.coordination.Flicker"
_TRAIT_SUPERNOVA = "anomaly.cohesion.size.Supernova"
_TRAIT_FREQUENT_CHANGER = "anomaly.cohesion.size.FrequentChanger"
_TRAIT_HIBERNATOR = "anomaly.cohesion.activity.Hibernator"
_TRAIT_AWAKENING = "anomaly.cohesion.activity.Awakening"
_TRAIT_EROSION = "anomaly.cohesion.activity.Erosion"


@METRICS.register
class AnomalyCohesionMetric(Metric):
    name: ClassVar[str] = "anomaly.cohesion"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=[
            _TRAIT_BAZAAR, _TRAIT_CATHEDRAL, _TRAIT_PULSAR, _TRAIT_FLICKER,
            _TRAIT_SUPERNOVA, _TRAIT_FREQUENT_CHANGER,
            _TRAIT_HIBERNATOR, _TRAIT_AWAKENING, _TRAIT_EROSION,
        ]
    )
    config_fields: ClassVar[list[str]] = [
        "bazaar_distinct_authors_min",
        "cathedral_dominance_ratio", "cathedral_min_recent_commits",
        "pulsar_cv_min", "pulsar_min_commits", "pulsar_min_intervals",
        "supernova_net_churn_min",
        "frequent_changer_lifetime_min", "frequent_changer_recent_min",
        "flicker_cv_min", "flicker_min_recent_commits",
        "hibernator_min_lifetime_commits",
        "awakening_min_dormant_weeks", "awakening_recent_commits_min",
        "erosion_window_weeks", "erosion_trend_max",
        "recent_window_days",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        files = _safe_iter(getattr(graph, "files", None))
        if not files:
            return

        commits_reg = getattr(graph, "commits", None)
        if commits_reg is None:
            return

        cutoff = _resolve_recent_cutoff(graph, commits_reg, config)
        cfg = _CohesionConfig.from_config(config)
        changes_by_file = _changes_by_file_index(graph)

        for file_ in files:
            file_ref = file_.ref()
            file_changes = list(changes_by_file(file_ref))
            if not file_changes:
                continue
            triples = _collect_triples(commits_reg, file_changes)
            if not triples:
                continue
            dates = sorted(d for d, _, _ in triples)
            recent_dates = (
                [d for d in dates if cutoff is None or d >= cutoff]
                if cutoff is not None else list(dates)
            )

            yield from _emit_bazaar_cathedral(file_ref, triples, cutoff, cfg)
            yield from _emit_pulsar(file_ref, dates, cfg)
            yield from _emit_flicker(file_ref, recent_dates, cfg)
            yield from _emit_supernova(graph, file_ref, file_changes, cfg)
            yield from _emit_frequent_changer(file_ref, dates, recent_dates, cfg)
            yield from _emit_hibernator(file_ref, dates, recent_dates, cfg)
            yield from _emit_awakening(file_ref, dates, recent_dates, cutoff, cfg)
            yield from _emit_erosion(graph, file_ref, cfg)


# ----------------------------------------------------------------------
# Coordination — Bazaar / Cathedral
# ----------------------------------------------------------------------
def _emit_bazaar_cathedral(
    file_ref: EntityRef,
    triples: list[tuple[datetime, EntityRef, Any]],
    cutoff: Optional[datetime],
    cfg: "_CohesionConfig",
) -> Iterable[Trait]:
    recent = [(d, a) for d, a, _ in triples if cutoff is None or d >= cutoff]
    if not recent:
        return
    counts: dict[str, int] = {}
    for _, author_ref in recent:
        counts[author_ref.id] = counts.get(author_ref.id, 0) + 1
    total = sum(counts.values())
    if len(counts) >= cfg.bazaar_distinct_authors_min:
        yield Trait(
            id=f"trait:{_TRAIT_BAZAAR}:{file_ref.kind.value}/{file_ref.id}",
            target=file_ref,
            family=TraitFamily.COHESION,
            name=_TRAIT_BAZAAR,
            severity=float(len(counts)),
            evidence={
                "distinct_authors_recent": int(len(counts)),
                "threshold": int(cfg.bazaar_distinct_authors_min),
            },
        )
    if total >= cfg.cathedral_min_recent_commits and counts:
        top_author, top_count = max(counts.items(), key=lambda kv: kv[1])
        dominance = top_count / total
        if dominance >= cfg.cathedral_dominance_ratio:
            yield Trait(
                id=f"trait:{_TRAIT_CATHEDRAL}:{file_ref.kind.value}/{file_ref.id}",
                target=file_ref,
                family=TraitFamily.COHESION,
                name=_TRAIT_CATHEDRAL,
                severity=round(dominance, 3),
                evidence={
                    "dominant_author": top_author,
                    "dominance_ratio": round(dominance, 3),
                    "threshold": float(cfg.cathedral_dominance_ratio),
                    "recent_commits": int(total),
                },
            )


# ----------------------------------------------------------------------
# Coordination — Pulsar (lifetime) / Flicker (recent window)
# ----------------------------------------------------------------------
def _emit_pulsar(
    file_ref: EntityRef, dates: list[datetime], cfg: "_CohesionConfig",
) -> Iterable[Trait]:
    if len(dates) < cfg.pulsar_min_commits:
        return
    gaps = _gaps(dates)
    if len(gaps) < cfg.pulsar_min_intervals:
        return
    cv = _coeff_of_variation(gaps)
    if cv is None or cv < cfg.pulsar_cv_min:
        return
    yield Trait(
        id=f"trait:{_TRAIT_PULSAR}:{file_ref.kind.value}/{file_ref.id}",
        target=file_ref, family=TraitFamily.COHESION,
        name=_TRAIT_PULSAR, severity=round(cv, 3),
        evidence={
            "interval_cv": round(cv, 3),
            "threshold": float(cfg.pulsar_cv_min),
            "commits": int(len(dates)),
        },
    )


def _emit_flicker(
    file_ref: EntityRef, recent_dates: list[datetime], cfg: "_CohesionConfig",
) -> Iterable[Trait]:
    if len(recent_dates) < cfg.flicker_min_recent_commits:
        return
    gaps = _gaps(sorted(recent_dates))
    if len(gaps) < 3:
        return
    cv = _coeff_of_variation(gaps)
    if cv is None or cv < cfg.flicker_cv_min:
        return
    yield Trait(
        id=f"trait:{_TRAIT_FLICKER}:{file_ref.kind.value}/{file_ref.id}",
        target=file_ref, family=TraitFamily.COHESION,
        name=_TRAIT_FLICKER, severity=round(cv, 3),
        evidence={
            "recent_interval_cv": round(cv, 3),
            "recent_commits": int(len(recent_dates)),
            "recent_gaps": int(len(gaps)),
            "threshold": float(cfg.flicker_cv_min),
        },
    )


# ----------------------------------------------------------------------
# Size — Supernova (proxy) / FrequentChanger
# ----------------------------------------------------------------------
def _emit_supernova(
    graph: Any, file_ref: EntityRef,
    file_changes: list[Any], cfg: "_CohesionConfig",
) -> Iterable[Trait]:
    net = sum(change_net_churn(graph, ch) for ch in file_changes)
    if net < cfg.supernova_net_churn_min:
        return
    yield Trait(
        id=f"trait:{_TRAIT_SUPERNOVA}:{file_ref.kind.value}/{file_ref.id}",
        target=file_ref, family=TraitFamily.COHESION,
        name=_TRAIT_SUPERNOVA, severity=float(net), is_proxy=True,
        evidence={
            "proxy": True,
            "note": "net-churn proxy, not absolute LOC",
            "net_churn": int(net),
            "threshold": int(cfg.supernova_net_churn_min),
        },
    )


def _emit_frequent_changer(
    file_ref: EntityRef, dates: list[datetime],
    recent_dates: list[datetime], cfg: "_CohesionConfig",
) -> Iterable[Trait]:
    lifetime = len(dates)
    recent_total = len(recent_dates)
    fires_lifetime = lifetime >= cfg.frequent_changer_lifetime_min
    fires_recent = recent_total >= cfg.frequent_changer_recent_min
    if not (fires_lifetime or fires_recent):
        return
    basis = "lifetime" if fires_lifetime else "recent"
    yield Trait(
        id=f"trait:{_TRAIT_FREQUENT_CHANGER}:{file_ref.kind.value}/{file_ref.id}",
        target=file_ref, family=TraitFamily.COHESION,
        name=_TRAIT_FREQUENT_CHANGER, severity=float(lifetime),
        evidence={
            "basis": basis,
            "lifetime_commits": int(lifetime),
            "recent_commits": int(recent_total),
            "lifetime_threshold": int(cfg.frequent_changer_lifetime_min),
            "recent_threshold": int(cfg.frequent_changer_recent_min),
        },
    )


# ----------------------------------------------------------------------
# Activity — Hibernator / Awakening / Erosion (Chunk 15b)
# ----------------------------------------------------------------------
def _emit_hibernator(
    file_ref: EntityRef,
    dates: list[datetime],
    recent_dates: list[datetime],
    cfg: "_CohesionConfig",
) -> Iterable[Trait]:
    """File with lifetime activity ≥ threshold but zero recent activity."""
    lifetime = len(dates)
    if lifetime < cfg.hibernator_min_lifetime_commits:
        return
    if recent_dates:
        return
    last_commit = max(dates)
    yield Trait(
        id=f"trait:{_TRAIT_HIBERNATOR}:{file_ref.kind.value}/{file_ref.id}",
        target=file_ref, family=TraitFamily.COHESION,
        name=_TRAIT_HIBERNATOR, severity=float(lifetime),
        evidence={
            "lifetime_commits": int(lifetime),
            "recent_commits": 0,
            "last_commit": last_commit.isoformat(),
            "threshold": int(cfg.hibernator_min_lifetime_commits),
        },
    )


def _emit_awakening(
    file_ref: EntityRef,
    dates: list[datetime],
    recent_dates: list[datetime],
    cutoff: Optional[datetime],
    cfg: "_CohesionConfig",
) -> Iterable[Trait]:
    """File dormant ≥ N weeks before the recent window, recently reactivated.

    Dormant span is measured between the last pre-cutoff commit and the
    first recent commit. Requires at least one historical commit; cutoff
    must be defined (no recent-window anchor = nothing to compare against).

    .. note::

       **Deliberate semantic upgrade vs legacy** — legacy v1
       (``tagger/anomaly_cohesion.py``) measured the dormant span as
       ``cutoff - last_before``; this implementation measures
       ``first_recent - last_before``. The two are identical when the
       first recent commit lands at the cutoff itself; the new formula
       yields a *longer* dormancy when the first recent commit lands
       deeper inside the recent window — which is the dormancy a human
       reader would intuitively report ("how long was the file actually
       silent before the next touch?"). No false negatives vs legacy
       (every legacy hit still fires here since
       ``first_recent >= cutoff``); evidence span is strictly richer.
       Pinned by ``test_awakening_emitted_after_dormancy`` and
       ``test_a21_file_traits.py::test_awakening_emitted_when_dormant_then_recent``.
    """
    if cutoff is None:
        return
    if len(recent_dates) < cfg.awakening_recent_commits_min:
        return
    pre_cutoff = [d for d in dates if d < cutoff]
    if not pre_cutoff:
        return
    last_old = max(pre_cutoff)
    first_recent = min(recent_dates)
    dormant_span = first_recent - last_old
    dormant_days = dormant_span.total_seconds() / 86400.0
    dormant_threshold_days = cfg.awakening_min_dormant_weeks * 7
    if dormant_days < dormant_threshold_days:
        return
    yield Trait(
        id=f"trait:{_TRAIT_AWAKENING}:{file_ref.kind.value}/{file_ref.id}",
        target=file_ref, family=TraitFamily.COHESION,
        name=_TRAIT_AWAKENING, severity=round(dormant_days, 1),
        evidence={
            "dormant_days": int(dormant_days),
            "dormant_threshold_days": int(dormant_threshold_days),
            "recent_commits": int(len(recent_dates)),
            "last_dormant_commit": last_old.isoformat(),
            "first_recent_commit": first_recent.isoformat(),
        },
    )


def _emit_erosion(
    graph: Any, file_ref: EntityRef, cfg: "_CohesionConfig",
) -> Iterable[Trait]:
    """Linearly declining per-window commit cadence (slope ≤ erosion_trend_max).

    Uses ``time_bucketed_commits(fill_gaps=True)`` so dormant buckets
    register as zeros; ``linear_slope`` fits a least-squares line through
    the per-bucket counts. Requires ≥ 3 buckets so the slope is meaningful.

    .. note::

       **Deliberate gate loosening vs legacy** — legacy v1
       (``tagger/anomaly_cohesion.py``) required ``len(buckets) >= 4``;
       this implementation requires ``>= 3``. Three points is the smallest
       sample where a least-squares slope is non-trivial (two points fit
       any line perfectly, so the slope carries no signal). The looser
       gate lets Erosion flag shorter histories whose declining trend is
       still well-defined; downstream consumers that require longer
       evidence can re-filter on ``evidence["buckets"]``. This is a
       widening, not a behavioural break — every legacy hit (≥ 4
       buckets) still fires here. Pinned by
       ``test_erosion_emitted_on_declining_cadence`` (5 buckets).
    """
    buckets = time_bucketed_commits(
        graph, file_ref, cfg.erosion_window_weeks, fill_gaps=True,
    )
    if len(buckets) < 3:
        return
    counts = [float(c) for _, c in buckets]
    slope = linear_slope(counts)
    if slope is None:
        return
    if slope > cfg.erosion_trend_max:
        return
    yield Trait(
        id=f"trait:{_TRAIT_EROSION}:{file_ref.kind.value}/{file_ref.id}",
        target=file_ref, family=TraitFamily.COHESION,
        name=_TRAIT_EROSION, severity=round(-slope, 3),
        evidence={
            "slope": round(slope, 4),
            "threshold": float(cfg.erosion_trend_max),
            "window_weeks": int(cfg.erosion_window_weeks),
            "buckets": int(len(buckets)),
            "first_bucket": buckets[0][0].isoformat(),
            "last_bucket": buckets[-1][0].isoformat(),
        },
    )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
class _CohesionConfig:
    """Cached snapshot of the 16 config knobs this metric reads."""

    __slots__ = (
        "bazaar_distinct_authors_min",
        "cathedral_dominance_ratio", "cathedral_min_recent_commits",
        "pulsar_cv_min", "pulsar_min_commits", "pulsar_min_intervals",
        "supernova_net_churn_min",
        "frequent_changer_lifetime_min", "frequent_changer_recent_min",
        "flicker_cv_min", "flicker_min_recent_commits",
        "hibernator_min_lifetime_commits",
        "awakening_min_dormant_weeks", "awakening_recent_commits_min",
        "erosion_window_weeks", "erosion_trend_max",
    )

    @classmethod
    def from_config(cls, config: Any) -> "_CohesionConfig":
        o = cls.__new__(cls)
        o.bazaar_distinct_authors_min = int(_cf(config, "bazaar_distinct_authors_min", 5))
        o.cathedral_dominance_ratio = float(_cf(config, "cathedral_dominance_ratio", 0.80))
        o.cathedral_min_recent_commits = int(_cf(config, "cathedral_min_recent_commits", 4))
        o.pulsar_cv_min = float(_cf(config, "pulsar_cv_min", 1.0))
        o.pulsar_min_commits = int(_cf(config, "pulsar_min_commits", 6))
        o.pulsar_min_intervals = int(_cf(config, "pulsar_min_intervals", 3))
        o.supernova_net_churn_min = int(_cf(config, "supernova_net_churn_min", 5000))
        o.frequent_changer_lifetime_min = int(_cf(config, "frequent_changer_lifetime_min", 50))
        o.frequent_changer_recent_min = int(_cf(config, "frequent_changer_recent_min", 10))
        o.flicker_cv_min = float(_cf(config, "flicker_cv_min", 1.2))
        o.flicker_min_recent_commits = int(_cf(config, "flicker_min_recent_commits", 4))
        o.hibernator_min_lifetime_commits = int(_cf(config, "hibernator_min_lifetime_commits", 5))
        o.awakening_min_dormant_weeks = int(_cf(config, "awakening_min_dormant_weeks", 12))
        o.awakening_recent_commits_min = int(_cf(config, "awakening_recent_commits_min", 1))
        o.erosion_window_weeks = int(_cf(config, "erosion_window_weeks", 4))
        o.erosion_trend_max = float(_cf(config, "erosion_trend_max", -0.5))
        return o


def _cf(config: Any, field: str, default: Any) -> Any:
    if config is None:
        return default
    return getattr(config, field, default)


def _safe_iter(reg: Any) -> list[Any]:
    if reg is None:
        return []
    try:
        return list(reg)
    except TypeError:
        return []


def _changes_by_file_index(graph: Any):
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _ref: ()
    by_file = getattr(changes, "by_file", None)
    if by_file is not None:
        return lambda file_ref: by_file[file_ref]
    return lambda file_ref: tuple(
        ch for ch in changes if getattr(ch, "file_ref", None) == file_ref
    )


def _collect_triples(
    commits_reg: Any, file_changes: list[Any],
) -> list[tuple[datetime, EntityRef, Any]]:
    out: list[tuple[datetime, EntityRef, Any]] = []
    for ch in file_changes:
        commit_ref = getattr(ch, "commit_ref", None)
        if commit_ref is None:
            continue
        commit = commits_reg.get(commit_ref.id) if hasattr(commits_reg, "get") else None
        if commit is None:
            continue
        d = ensure_aware(getattr(commit, "author_date", None))
        a = getattr(commit, "author_ref", None)
        if d is None or a is None:
            continue
        out.append((d, a, ch))
    return out


def _gaps(sorted_dates: list[datetime]) -> list[float]:
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


def _resolve_recent_cutoff(graph: Any, commits_reg: Any, config: Any) -> Optional[datetime]:
    """Mirror :func:`author_classifiers._resolve_recent_cutoff` priority."""
    explicit = getattr(graph, "recent_cutoff", None)
    if explicit is not None:
        return ensure_aware(explicit)
    window_days = int(_cf(config, "recent_window_days", 90))
    latest: Optional[datetime] = None
    try:
        for c in commits_reg:
            d = ensure_aware(
                getattr(c, "author_date", None)
                or getattr(c, "committer_date", None)
            )
            if d is None:
                continue
            if latest is None or d > latest:
                latest = d
    except TypeError:
        return None
    if latest is None:
        return None
    return latest - timedelta(days=window_days)


__all__ = ["AnomalyCohesionMetric"]
