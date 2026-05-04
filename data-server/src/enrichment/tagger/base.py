"""Tagger protocol — the Python analogue of dx's `Anomaly.check(SourceFile)`.

Each tagger is a pure function over the graph. `compose_tags` merges their
outputs into the shared `tags_by_entity` dict, stacking classifiers and
concatenating traits when multiple taggers target the same entity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional, Protocol

from src.common.lizard_models import FileMetric
from src.enrichment.config import EnrichmentConfig
from src.enrichment.models import EntityTags, Trait


@dataclass
class TaggingContext:
    """Everything a tagger needs, computed once per pipeline run."""
    graph_data: dict
    config: EnrichmentConfig
    anchor_date: Optional[datetime]
    recent_cutoff: Optional[datetime]
    file_metric_map: dict[str, FileMetric] = field(default_factory=dict)


class Tagger(Protocol):
    """Runs over the graph and yields EntityTags rows to merge.

    Concrete taggers must also declare their identity as a class attribute so
    the enrichment registry (`src/enrichment/registry.py`) can introspect them:
      - Anomaly tagger: `TRAITS = [{"name", "entity", "family"}, ...]`
      - Classifier tagger: `CLASSIFIERS = [{"slot", "entity", "values"}, ...]`
    See `src/enrichment/claude.md` for the full contract.
    """

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]: ...


def compose_tags(
    taggers: list[Tagger],
    ctx: TaggingContext,
) -> dict[str, EntityTags]:
    """Run every tagger and merge their outputs keyed by entity.

    Classifier collisions prefer the last writer (taggers ordered specific→generic).
    Traits are concatenated without dedup — a single entity may carry the same
    family from different derivations with different evidence.
    """
    merged: dict[str, EntityTags] = {}

    for tagger in taggers:
        for tags in tagger.tag(ctx):
            key = tags.key
            existing = merged.get(key)
            if existing is None:
                merged[key] = tags
                continue
            existing.classifiers.update(tags.classifiers)
            existing.traits.extend(tags.traits)

    return merged


def make_trait(name: str, family: str, severity: float = 1.0, **evidence) -> Trait:
    """Convenience — build a Trait with arbitrary evidence keys."""
    return Trait(name=name, family=family, severity=severity, evidence=dict(evidence))
