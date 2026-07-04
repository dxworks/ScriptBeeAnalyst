"""Relations — first-class graph entities for cross-entity links.

Public API::

    from src.enrichment.relations import (
        Relation, WindowKind, RelationExtra,
        RelationRegistry, RelationBuilder,
        BUILDERS, BuilderRegistry,
    )

See §6 of ``architectural_changes.md`` for the design.
"""
from __future__ import annotations

from .builders import BUILDERS, BuilderRegistry, RelationBuilder
from .models import Relation, RelationExtra, WindowKind
from .registries import RelationRegistry

__all__ = [
    "BUILDERS",
    "BuilderRegistry",
    "Relation",
    "RelationExtra",
    "RelationBuilder",
    "RelationRegistry",
    "WindowKind",
]
