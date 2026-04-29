"""Enrichment layer: tags, relations, overview tables derived from Git/GitHub/JIRA graphs.

Port of dx's Tag / Anomaly / RelationExtractor / Table patterns (Java) into
Python, computed over the in-memory ScriptBeeAssistant graph rather than
Voyager output.
"""

from src.enrichment.models import (
    Classifier,
    Component,
    EntityTags,
    Enrichments,
    OverviewCell,
    OverviewRow,
    OverviewTable,
    Relation,
    RelationFile,
    Trait,
)
from src.enrichment.pipeline import compute_enrichments
from src.enrichment.repository import SupabaseEnrichmentRepository

__all__ = [
    "SupabaseEnrichmentRepository",
    "Classifier",
    "Component",
    "EntityTags",
    "Enrichments",
    "OverviewCell",
    "OverviewRow",
    "OverviewTable",
    "Relation",
    "RelationFile",
    "Trait",
    "compute_enrichments",
]
