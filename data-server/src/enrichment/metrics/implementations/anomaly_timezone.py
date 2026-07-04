"""Timezone anomaly metric — v2 port (Chunk 16).

Port of legacy ``src/enrichment/tagger/anomaly_timezone.py`` (~131 LOC).
Emits two :class:`Trait` rows in the :attr:`TraitFamily.COHESION` family:

* ``anomaly.cohesion.ZoneCrossroad`` — a file with commits across two or
  more UTC offsets, each carrying at least
  ``cfg.zonecrossroad_min_zone_commits`` commits on the file. dx default
  (``ZoneCrossroad.java:16``) is 10 commits per "significant" zone.
* ``anomaly.cohesion.ConcurrentZoneCrossroad`` — counts ``(year, month)``
  periods during which two or more zones were *simultaneously* active.
  Severity tiers by
  ``cfg.concurrent_zonecrossroad_strict_threshold`` (dx default ~5).

Reads:

* ``graph.files``              — file population.
* ``graph.changes.by_file``    — per-file change walks.
* ``graph.commits.get``        — commit metadata (``author_date``).

The UTC offset is taken from each commit's ``author_date.utcoffset()``.
Tz-naive datetimes fall through ``ensure_aware`` as UTC; this keeps the
metric robust against ingest sources that drop tz info — at the cost of
under-counting cross-zone activity in those graphs (documented).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tags import Trait, TraitFamily

if TYPE_CHECKING:
    from src.common.kernel import Graph


_TRAIT_ZONE_CROSSROAD = "anomaly.cohesion.ZoneCrossroad"
_TRAIT_CONCURRENT_ZONE_CROSSROAD = "anomaly.cohesion.ConcurrentZoneCrossroad"

_DEFAULT_MIN_FILE_COMMITS = 20
_DEFAULT_MIN_ZONE_COMMITS = 10
_DEFAULT_CONCURRENT_STRICT_THRESHOLD = 5


@METRICS.register
class AnomalyTimezoneMetric(Metric):
    name: ClassVar[str] = "anomaly.timezone"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=[
            _TRAIT_ZONE_CROSSROAD,
            _TRAIT_CONCURRENT_ZONE_CROSSROAD,
        ]
    )
    config_fields: ClassVar[list[str]] = [
        "zonecrossroad_min_file_commits",
        "zonecrossroad_min_zone_commits",
        "concurrent_zonecrossroad_strict_threshold",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        files = _safe_iter(getattr(graph, "files", None))
        if not files:
            return

        commits_reg = getattr(graph, "commits", None)
        if commits_reg is None:
            return

        min_file_commits = int(_config_field(
            config, "zonecrossroad_min_file_commits", _DEFAULT_MIN_FILE_COMMITS
        ))
        min_zone_commits = int(_config_field(
            config, "zonecrossroad_min_zone_commits", _DEFAULT_MIN_ZONE_COMMITS
        ))
        strict_threshold = int(_config_field(
            config, "concurrent_zonecrossroad_strict_threshold",
            _DEFAULT_CONCURRENT_STRICT_THRESHOLD,
        ))
        changes_by_file = _changes_by_file_index(graph)

        for file_ in files:
            file_ref = file_.ref()
            file_changes = list(changes_by_file(file_ref))
            if not file_changes:
                continue

            zone_commit_count: dict[int, int] = defaultdict(int)
            zones_per_period: dict[tuple[int, int], set[int]] = defaultdict(set)

            total_commits = 0
            for ch in file_changes:
                commit_ref = getattr(ch, "commit_ref", None)
                if commit_ref is None:
                    continue
                commit = commits_reg.get(commit_ref.id) if hasattr(commits_reg, "get") else None
                if commit is None:
                    continue
                d = ensure_aware(getattr(commit, "author_date", None))
                if d is None:
                    continue
                total_commits += 1
                zone = _utc_offset_minutes(d)
                period = (d.year, d.month)
                zone_commit_count[zone] += 1
                zones_per_period[period].add(zone)

            # dx pre-filter (``ZoneCrossroad.java:29``): the metric only runs
            # on files with enough lifetime commits for the zone signal to be
            # meaningful. Without this gate ScriptBee fires on very small
            # files, inflating the trait count. The knob is editable so a
            # user can dial back to the previous permissive behaviour (``2``)
            # if they want.
            if total_commits < min_file_commits:
                continue

            significant_zones = {
                z for z, c in zone_commit_count.items() if c >= min_zone_commits
            }

            if len(significant_zones) >= 2:
                severity = _zonecrossroad_severity(len(significant_zones))
                yield Trait(
                    id=f"trait:{_TRAIT_ZONE_CROSSROAD}:{file_ref.kind.value}/{file_ref.id}",
                    target=file_ref,
                    family=TraitFamily.COHESION,
                    name=_TRAIT_ZONE_CROSSROAD,
                    severity=float(severity),
                    evidence={
                        "zones_with_activity": int(len(significant_zones)),
                        "total_zones_seen": int(len(zone_commit_count)),
                        "threshold": int(min_zone_commits),
                    },
                )

            concurrent_periods = sum(
                1 for zones in zones_per_period.values() if len(zones) >= 2
            )
            if concurrent_periods >= 1 and len(significant_zones) >= 2:
                severity = _concurrent_severity(concurrent_periods, strict_threshold)
                yield Trait(
                    id=f"trait:{_TRAIT_CONCURRENT_ZONE_CROSSROAD}:{file_ref.kind.value}/{file_ref.id}",
                    target=file_ref,
                    family=TraitFamily.COHESION,
                    name=_TRAIT_CONCURRENT_ZONE_CROSSROAD,
                    severity=float(severity),
                    evidence={
                        "concurrent_periods": int(concurrent_periods),
                        "threshold": int(strict_threshold),
                    },
                )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_iter(reg: Any) -> list[Any]:
    if reg is None:
        return []
    try:
        return list(reg)
    except TypeError:
        return []


def _config_field(config: Any, field: str, default: Any) -> Any:
    if config is None:
        return default
    return getattr(config, field, default)


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


def _utc_offset_minutes(dt: datetime) -> int:
    """Return the UTC offset of ``dt`` in whole minutes; 0 for tz-naive."""
    off = dt.utcoffset()
    if off is None:
        return 0
    return int(off.total_seconds() // 60)


def _zonecrossroad_severity(zone_count: int) -> int:
    """dx port — severity scales with raw zone count, clamped to 10."""
    return min(zone_count, 10)


def _concurrent_severity(periods: int, strict_threshold: int) -> int:
    """dx port — severity tiered by count of concurrent-activity periods.

    * ``periods >= 2 * strict_threshold``       → 10 (max)
    * ``strict_threshold <= periods < 2 * st``  → 5 + min(periods - st, 4)
    * ``periods < strict_threshold``            → min(periods, 5)
    """
    if periods >= strict_threshold * 2:
        return 10
    if periods >= strict_threshold:
        return 5 + min(periods - strict_threshold, 4)
    return min(periods, 5)


__all__ = ["AnomalyTimezoneMetric"]
