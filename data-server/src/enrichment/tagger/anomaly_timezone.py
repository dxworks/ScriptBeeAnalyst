"""anomaly.cohesion.* — ZoneCrossroad / ConcurrentZoneCrossroad timezone anomalies.

Implements §7 (ZoneCrossroad / ConcurrentZoneCrossroad) of
communication/B2_codeframe/index_step_general.md.

dx port (ZoneCrossroad.java:15–155):
  - Per-file commits grouped by `(year, month)` period and by UTC offset zone.
  - A "significant" zone has at least cfg.zonecrossroad_min_zone_commits
    commits on the file (dx default: 10).
  - ZoneCrossroad: file has commits in >= 2 significant zones AND >= 2 commits
    total. Severity tiered by raw zone count.
  - ConcurrentZoneCrossroad: number of (year, month) periods where >= 2 zones
    are simultaneously active. Severity tiered by concurrent-period count.

The data source is the existing GitProject (each commit's author_date carries
tzinfo); no JaFax/CodeFrame ingest is required for this tagger to run.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timezone
from typing import Iterable

from src.enrichment.models import EntityTags, Trait
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext, make_trait
from src.enrichment.tagger.file_classifiers import _file_id


class TimezoneAnomalyTagger:
    """Detects file-level timezone anomalies (Zone / ConcurrentZone Crossroad)."""

    TRAITS = [
        {"name": "anomaly.cohesion.ZoneCrossroad",           "entity": "file", "family": "cohesion"},
        {"name": "anomaly.cohesion.ConcurrentZoneCrossroad", "entity": "file", "family": "cohesion"},
    ]

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        cfg = ctx.config
        min_zone_commits = cfg.zonecrossroad_min_zone_commits

        out: list[EntityTags] = []

        for file_ in git.file_registry.all:
            fid = _file_id(file_)
            if fid is None:
                continue

            # zone -> total commit count, period -> set of zones, zone -> set of periods
            zone_commit_count: dict[int, int] = defaultdict(int)
            zones_per_period: dict[tuple[int, int], set[int]] = defaultdict(set)

            total_commits = 0
            for ch in file_.changes or []:
                commit = getattr(ch, "commit", None)
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

            if total_commits < 2:
                continue

            significant_zones = {
                z for z, c in zone_commit_count.items() if c >= min_zone_commits
            }
            traits: list[Trait] = []

            if len(significant_zones) >= 2:
                severity = _zonecrossroad_severity(len(significant_zones))
                traits.append(make_trait(
                    "anomaly.cohesion.ZoneCrossroad",
                    family="cohesion",
                    severity=float(severity),
                    zones_with_activity=len(significant_zones),
                    total_zones_seen=len(zone_commit_count),
                    threshold=min_zone_commits,
                ))

            concurrent_periods = sum(
                1 for zones in zones_per_period.values() if len(zones) >= 2
            )
            if concurrent_periods >= 1 and len(significant_zones) >= 2:
                severity = _concurrent_severity(
                    concurrent_periods,
                    cfg.concurrent_zonecrossroad_strict_threshold,
                )
                traits.append(make_trait(
                    "anomaly.cohesion.ConcurrentZoneCrossroad",
                    family="cohesion",
                    severity=float(severity),
                    concurrent_periods=concurrent_periods,
                    threshold=cfg.concurrent_zonecrossroad_strict_threshold,
                ))

            if traits:
                out.append(EntityTags(entity_kind="file", entity_id=fid, traits=traits))

        return out


def _utc_offset_minutes(dt) -> int:
    """Return the UTC offset of `dt` in whole minutes; 0 for tz-naive (defensive)."""
    off = dt.utcoffset()
    if off is None:
        return 0
    return int(off.total_seconds() // 60)


def _zonecrossroad_severity(zone_count: int) -> int:
    """dx port — severity scales with raw zone count, clamped to 10."""
    return min(zone_count, 10)


def _concurrent_severity(periods: int, strict_threshold: int) -> int:
    """dx port — severity tiered by count of concurrent-activity periods."""
    if periods >= strict_threshold * 2:
        return 10
    if periods >= strict_threshold:
        return 5 + min(periods - strict_threshold, 4)
    return min(periods, 5)
