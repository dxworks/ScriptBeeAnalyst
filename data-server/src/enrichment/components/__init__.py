"""Folder-based components: optional `components.mapping.json` override + heuristic fallback."""
from src.enrichment.components.mapping import (
    ComponentMapping,
    ComponentSpec,
    load_component_mapping,
)
from src.enrichment.components.resolver import (
    ComponentResolver,
    OTHER_COMPONENT,
    top_folder_of,
)

__all__ = [
    "ComponentMapping",
    "ComponentSpec",
    "ComponentResolver",
    "OTHER_COMPONENT",
    "load_component_mapping",
    "top_folder_of",
]
