"""End-to-end enrichment pipeline: graph_data → Enrichments.

Called once at project load (server.load_project). Phase 2 adds:
  - Issue / PR classifiers.
  - Anomaly traits (knowledge, cohesion, structuring, testing).
  - Author↔file ownership and Issue↔file relations.
  - Authorship and Testing overview tables.
  - Optional Supabase persistence via `repository.SupabaseEnrichmentRepository`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.enrichment.config import DEFAULT_CONFIG, EnrichmentConfig
from src.enrichment.models import Enrichments
from src.enrichment.overview.authorship_table import AuthorshipTableBuilder
from src.enrichment.overview.pace_table import PaceTableBuilder
from src.enrichment.overview.testing_table import TestingTableBuilder
from src.enrichment.recent_window import (
    ensure_aware,
    latest_commit_date,
    recent_cutoff,
)
from src.enrichment.relations.cochange import FileCoChangeExtractor
from src.enrichment.relations.issue_file import IssueFileExtractor
from src.enrichment.relations.ownership import OwnershipExtractor
from src.enrichment.tagger.anomaly_cohesion import CohesionAnomalyTagger
from src.enrichment.tagger.anomaly_knowledge import KnowledgeAnomalyTagger
from src.enrichment.tagger.anomaly_structuring import StructuringAnomalyTagger
from src.enrichment.tagger.anomaly_testing import TestingAnomalyTagger
from src.enrichment.tagger.author_classifiers import AuthorClassifiersTagger
from src.enrichment.tagger.base import TaggingContext, compose_tags
from src.enrichment.tagger.commit_classifiers import CommitClassifiersTagger
from src.enrichment.tagger.file_classifiers import FileClassifiersTagger
from src.enrichment.tagger.issue_pr_classifiers import (
    IssueClassifiersTagger,
    PullRequestClassifiersTagger,
)
from src.logger import get_logger

LOG = get_logger(__name__)


def compute_enrichments(
    graph_data: dict,
    config: Optional[EnrichmentConfig] = None,
) -> Enrichments:
    """Run every Phase 1 + Phase 2 tagger / extractor / overview over the graph."""
    cfg = config or DEFAULT_CONFIG

    anchor = _resolve_anchor(graph_data)
    cutoff = recent_cutoff(anchor, cfg.recent_window_days)

    ctx = TaggingContext(
        graph_data=graph_data,
        config=cfg,
        anchor_date=anchor,
        recent_cutoff=cutoff,
    )

    # Pass 1: classifier-only taggers (no inter-dependence).
    classifier_taggers = [
        CommitClassifiersTagger(),
        FileClassifiersTagger(),
        AuthorClassifiersTagger(),
        IssueClassifiersTagger(),
        PullRequestClassifiersTagger(),
    ]
    tags_by_entity = compose_tags(classifier_taggers, ctx)

    # Pass 2: anomaly traits (BugMagnet reads commit classifiers from pass 1).
    trait_taggers = [
        KnowledgeAnomalyTagger(),
        CohesionAnomalyTagger(),
        StructuringAnomalyTagger(),
        TestingAnomalyTagger(tags_by_entity),
    ]
    trait_results = compose_tags(trait_taggers, ctx)
    for key, value in trait_results.items():
        existing = tags_by_entity.get(key)
        if existing is None:
            tags_by_entity[key] = value
        else:
            existing.classifiers.update(value.classifiers)
            existing.traits.extend(value.traits)

    # Relations
    relations = []
    relations.extend(FileCoChangeExtractor().extract(ctx))
    relations.extend(OwnershipExtractor().extract(ctx))
    relations.extend(IssueFileExtractor().extract(ctx))

    # Overview tables
    overviews = [
        PaceTableBuilder().build(ctx, tags_by_entity),
        AuthorshipTableBuilder().build(ctx, tags_by_entity),
        TestingTableBuilder().build(ctx, tags_by_entity),
    ]

    LOG.info(
        "Enrichment computed: %d tagged entities, %d relation files, %d overviews, "
        "anchor=%s, recent_window=%dd",
        len(tags_by_entity), len(relations), len(overviews),
        anchor.isoformat() if anchor else "none",
        cfg.recent_window_days,
    )

    return Enrichments(
        generated_at=datetime.now(timezone.utc),
        recent_window_days=cfg.recent_window_days,
        components=[],  # Phase 3
        tags_by_entity=tags_by_entity,
        relations=relations,
        overviews=overviews,
    )


def _resolve_anchor(graph_data: dict):
    git = graph_data.get("git")
    if git is None:
        return None
    return ensure_aware(latest_commit_date(git.git_commit_registry.all))
