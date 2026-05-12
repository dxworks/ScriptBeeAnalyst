"""Relations — first-class graph entities for cross-entity links.

Public API for Chunks 7/9 (metric port + MCP sandbox)::

    from src.enrichment.relations_v2 import (
        Relation, WindowKind, RelationExtra,
        RelationRegistry, RelationBuilder,
    )

This package lives under the ``relations_v2`` name (NOT ``relations``)
because the legacy package ``src/enrichment/relations/`` still ships the
ProjectLinker-style builders that Chunk 7 will port. Once Chunk 10 deletes
the legacy code, this package will be renamed to ``relations`` to match
the plan §13 module layout. See the Chunk-3 handoff for the rationale.

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
