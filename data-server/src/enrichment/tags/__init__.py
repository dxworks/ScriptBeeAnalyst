"""Tags — first-class graph entities for traits and classifiers.

Public API for Chunks 7/9 (metric port + MCP sandbox)::

    from src.enrichment.tags import (
        Tag, Trait, Classifier, TraitFamily, EvidenceValue,
        TraitRegistry, ClassifierRegistry,
    )

See §5 of ``architectural_changes.md`` for the design and the Chunk-3
handoff (``plan/task_4/handoffs/chunk_03_tags_relations_metrics.md``) for
worked examples + membership audit of :class:`TraitFamily`.
"""
from __future__ import annotations

from .base import Classifier, EvidenceValue, Tag, Trait, TraitFamily
from .registries import ClassifierRegistry, TraitRegistry

__all__ = [
    "Tag",
    "Trait",
    "Classifier",
    "TraitFamily",
    "EvidenceValue",
    "TraitRegistry",
    "ClassifierRegistry",
]
