"""Components domain — folder-grouping :class:`Component` entities.

Per plan §5/§7, :class:`Component` is the one entity that mixes "data"
and "tag-like aggregation": it's an :class:`Entity` (lives in
:class:`ComponentRegistry`) but its rows are computed at enrichment
time by :class:`ComponentResolverMetric`, not by a source-domain
transformer. See Chunk-7 handoff §"Components".
"""
from __future__ import annotations

from .models import Component
from .registries import ComponentRegistry
from .resolver import ComponentMapping, ComponentSpec, ComponentResolver

__all__ = [
    "Component",
    "ComponentMapping",
    "ComponentRegistry",
    "ComponentResolver",
    "ComponentSpec",
]
