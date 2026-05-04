"""anomaly.cohesion.size.DynamicBlob — high-LOC AND high-churn files.

Implements §7 of communication/B1_lizard/index_step_general.md.

dx port (DynamicBlob.java lines 41-56):
  - Fires only when LOC >= cfg.dynamicblob_loc_min AND
    file commit count >= cfg.dynamicblob_changes_min.
  - Severity = 1 + LOC bucket bonus (up to 5) + churn bucket bonus (up to 4),
    clamped to 10 to match dx AnomaliesRegistry.normalizedValue range.
"""
from __future__ import annotations

from typing import Iterable

from src.enrichment.models import EntityTags, Trait
from src.enrichment.tagger.base import TaggingContext, make_trait
from src.enrichment.tagger.file_classifiers import _file_id


class ComplexityAnomalyTagger:
    """DynamicBlob — high LOC × high recent churn.

    Fires on a file when the Lizard-derived LOC is >= cfg.dynamicblob_loc_min
    AND the file's lifetime change count is >= cfg.dynamicblob_changes_min.
    Severity follows dx's bucket formula: a base of 1 plus up to 5 from LOC
    buckets (>=2x, >=3x, >=5x of the LOC threshold) and up to 4 from churn
    buckets (>=1.5x, >=3x of the change threshold), capped at 10.
    """

    TRAITS = [
        {"name": "anomaly.cohesion.size.DynamicBlob", "entity": "file", "family": "cohesion"},
    ]

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        cfg = ctx.config
        out: list[EntityTags] = []

        for file_ in git.file_registry.all:
            fid = _file_id(file_)
            if fid is None:
                continue

            metric = ctx.file_metric_map.get(fid)
            if metric is None:
                continue

            change_count = sum(1 for ch in (file_.changes or []) if getattr(ch, "commit", None))

            if (
                metric.sum_nloc < cfg.dynamicblob_loc_min
                or change_count < cfg.dynamicblob_changes_min
            ):
                continue

            severity = _dynamicblob_severity(
                metric.sum_nloc, change_count,
                cfg.dynamicblob_loc_min, cfg.dynamicblob_changes_min,
            )

            traits: list[Trait] = [make_trait(
                "anomaly.cohesion.size.DynamicBlob",
                family="cohesion",
                severity=float(severity),
                loc=metric.sum_nloc,
                changes=change_count,
                max_ccn=metric.max_ccn,
                avg_ccn=metric.avg_ccn,
                threshold_loc=cfg.dynamicblob_loc_min,
                threshold_changes=cfg.dynamicblob_changes_min,
            )]
            out.append(EntityTags(entity_kind="file", entity_id=fid, traits=traits))

        return out


def _dynamicblob_severity(loc: int, changes: int, loc_min: int, changes_min: int) -> int:
    severity = 1
    if loc >= loc_min * 5:
        severity += 5
    elif loc >= loc_min * 3:
        severity += 3
    elif loc >= loc_min * 2:
        severity += 1

    if changes >= changes_min * 3:
        severity += 4
    elif changes >= changes_min * 1.5:
        severity += 2

    return min(severity, 10)
