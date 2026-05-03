"""End-to-end enrichment pipeline: graph_data → Enrichments.

Called once at project load (server.load_project). Phase 3 adds:
  - Components module (folder heuristic + optional `components.mapping.json`).
  - Cross-source relations (coauthor, pr.file, pr.reviewer, component cochange,
    issue.issue).
  - Proxy traits (Supernova, TestOrphan).
  - Components and Intent/Impact overviews.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.enrichment.components.mapping import load_component_mapping
from src.enrichment.components.resolver import ComponentResolver
from src.enrichment.config import DEFAULT_CONFIG, EnrichmentConfig
from src.enrichment.models import Enrichments
from src.enrichment.overview.authorship_table import AuthorshipTableBuilder
from src.enrichment.overview.components_table import ComponentsTableBuilder
from src.enrichment.overview.feature_traceability_table import (
    FeatureTraceabilityTableBuilder,
)
from src.enrichment.overview.intent_impact_table import IntentImpactTableBuilder
from src.enrichment.overview.knowledge_table import KnowledgeTableBuilder
from src.enrichment.overview.nature_table import NatureTableBuilder
from src.enrichment.overview.pace_table import PaceTableBuilder
from src.enrichment.overview.pr_lifecycle_table import PullRequestLifecycleTableBuilder
from src.enrichment.overview.testing_table import TestingTableBuilder
from src.enrichment.recent_window import (
    ensure_aware,
    latest_commit_date,
    recent_cutoff,
)
from src.enrichment.relations.coauthor import CoAuthorExtractor
from src.enrichment.relations.cochange import FileCoChangeExtractor
from src.enrichment.relations.cochange_author_shared_task_prefixes import (
    AuthorSharedTaskPrefixesExtractor,
)
from src.enrichment.relations.cochange_author_time_windowed import (
    AuthorTimeWindowedExtractor,
)
from src.enrichment.relations.cochange_component import ComponentCoChangeExtractor
from src.enrichment.relations.cochange_component_shared_devs import (
    ComponentSharedDevsCoChangeExtractor,
)
from src.enrichment.relations.cochange_component_shared_task_prefixes import (
    ComponentSharedTaskPrefixesExtractor,
)
from src.enrichment.relations.cochange_component_time_windowed import (
    ComponentTimeWindowedExtractor,
)
from src.enrichment.relations.cochange_file_shared_devs import (
    FileSharedDevsCoChangeExtractor,
)
from src.enrichment.relations.cochange_file_shared_task_prefixes import (
    FileSharedTaskPrefixesCoChangeExtractor,
)
from src.enrichment.relations.cochange_file_time_windowed import (
    FileTimeWindowedCoChangeExtractor,
)
from src.enrichment.relations.issue_file import IssueFileExtractor
from src.enrichment.relations.issue_issue import IssueIssueExtractor
from src.enrichment.relations.ownership import OwnershipExtractor
from src.enrichment.relations.pr_file import PullRequestFileExtractor
from src.enrichment.relations.pr_reviewer import PullRequestReviewerExtractor
from src.enrichment.relations.similarity_file_names import (
    FileNameSimilarityExtractor,
)
from src.enrichment.tagger.anomaly_cohesion import CohesionAnomalyTagger
from src.enrichment.tagger.anomaly_knowledge import KnowledgeAnomalyTagger
from src.enrichment.tagger.anomaly_structuring import StructuringAnomalyTagger
from src.enrichment.tagger.anomaly_testing import TestingAnomalyTagger
from src.enrichment.tagger.author_classifiers import AuthorClassifiersTagger
from src.enrichment.tagger.base import TaggingContext, compose_tags
from src.enrichment.tagger.commit_classifiers import CommitClassifiersTagger
from src.enrichment.tagger.file_classifiers import FileClassifiersTagger, _file_id
from src.enrichment.tagger.issue_pr_classifiers import (
    IssueClassifiersTagger,
    PullRequestClassifiersTagger,
)
from src.enrichment.tagger.pr_traits import StalledReviewTagger
from src.logger import get_logger

LOG = get_logger(__name__)


def compute_enrichments(
    graph_data: dict,
    config: Optional[EnrichmentConfig] = None,
) -> Enrichments:
    """Run every Phase 1 + 2 + 3 tagger / extractor / overview over the graph."""
    cfg = config or DEFAULT_CONFIG

    anchor = _resolve_anchor(graph_data)
    cutoff = recent_cutoff(anchor, cfg.recent_window_days)

    ctx = TaggingContext(
        graph_data=graph_data,
        config=cfg,
        anchor_date=anchor,
        recent_cutoff=cutoff,
    )

    # Components — folder heuristic + optional mapping override.
    mapping = load_component_mapping(cfg.components_mapping_path)
    resolver = ComponentResolver(mapping)
    file_paths = _list_file_paths(graph_data)
    components = resolver.build_components(file_paths)

    # Pass 1: classifier-only taggers (no inter-dependence).
    classifier_taggers = [
        CommitClassifiersTagger(),
        FileClassifiersTagger(),
        AuthorClassifiersTagger(),
        IssueClassifiersTagger(),
        PullRequestClassifiersTagger(),
    ]
    tags_by_entity = compose_tags(classifier_taggers, ctx)

    # Pass 2: anomaly traits (BugMagnet/TestOrphan/Supernova read pass-1 output).
    trait_taggers = [
        KnowledgeAnomalyTagger(tags_by_entity),
        CohesionAnomalyTagger(),
        StructuringAnomalyTagger(),
        TestingAnomalyTagger(tags_by_entity),
        StalledReviewTagger(),
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
    file_cochange = FileCoChangeExtractor().extract(ctx)
    relations.extend(file_cochange)
    relations.extend(OwnershipExtractor().extract(ctx))
    relations.extend(IssueFileExtractor().extract(ctx))
    # `coauthor.author-author` (strength = files both authors changed) covers
    # the dx-named `cochange.author-author.shared-files` signal — we keep the
    # existing kind name and skip a duplicate extractor (A2.3).
    relations.extend(CoAuthorExtractor().extract(ctx))
    relations.extend(PullRequestFileExtractor().extract(ctx))
    relations.extend(PullRequestReviewerExtractor().extract(ctx))
    relations.extend(IssueIssueExtractor().extract(ctx))

    # A2.3 — file-file family.
    file_shared_devs = FileSharedDevsCoChangeExtractor().extract(ctx)
    file_shared_prefixes = FileSharedTaskPrefixesCoChangeExtractor().extract(ctx)
    file_time_windowed = FileTimeWindowedCoChangeExtractor().extract(ctx)
    relations.extend(file_shared_devs)
    relations.extend(file_shared_prefixes)
    relations.extend(file_time_windowed)
    relations.extend(FileNameSimilarityExtractor().extract(ctx))

    # A2.3 — author-author family (shared-files duplicate skipped — see above).
    relations.extend(AuthorSharedTaskPrefixesExtractor().extract(ctx))
    relations.extend(AuthorTimeWindowedExtractor().extract(ctx))

    # Component-component cochange aggregates the file-file edges; depends on
    # `resolver` and `file_cochange` so it runs after both exist.
    relations.extend(
        ComponentCoChangeExtractor(resolver, file_cochange).extract(ctx)
    )
    # A2.3 — component aggregations MUST run after the file-file extractors
    # they depend on (file_shared_devs / file_shared_prefixes / file_time_windowed).
    relations.extend(
        ComponentSharedDevsCoChangeExtractor(resolver, file_shared_devs).extract(ctx)
    )
    relations.extend(
        ComponentSharedTaskPrefixesExtractor(resolver, file_shared_prefixes).extract(ctx)
    )
    relations.extend(
        ComponentTimeWindowedExtractor(resolver, file_time_windowed).extract(ctx)
    )

    # Overview tables
    overviews = [
        PaceTableBuilder().build(ctx, tags_by_entity),
        AuthorshipTableBuilder().build(ctx, tags_by_entity),
        TestingTableBuilder().build(ctx, tags_by_entity),
        ComponentsTableBuilder().build(ctx, tags_by_entity, components, resolver),
        IntentImpactTableBuilder().build(ctx, tags_by_entity),
        KnowledgeTableBuilder().build(ctx, tags_by_entity),
        NatureTableBuilder().build(ctx, tags_by_entity),
        FeatureTraceabilityTableBuilder().build(ctx, tags_by_entity),
        PullRequestLifecycleTableBuilder().build(ctx, tags_by_entity),
    ]

    LOG.info(
        "Enrichment computed: %d tagged entities, %d relation files, %d overviews, "
        "%d components, anchor=%s, recent_window=%dd",
        len(tags_by_entity), len(relations), len(overviews), len(components),
        anchor.isoformat() if anchor else "none",
        cfg.recent_window_days,
    )

    return Enrichments(
        generated_at=datetime.now(timezone.utc),
        recent_window_days=cfg.recent_window_days,
        components=components,
        tags_by_entity=tags_by_entity,
        relations=relations,
        overviews=overviews,
    )


def _resolve_anchor(graph_data: dict):
    git = graph_data.get("git")
    if git is None:
        return None
    return ensure_aware(latest_commit_date(git.git_commit_registry.all))


def _list_file_paths(graph_data: dict) -> list[str]:
    git = graph_data.get("git")
    if git is None:
        return []
    out: list[str] = []
    for f in git.file_registry.all:
        fid = _file_id(f)
        if fid:
            out.append(fid)
    return out
