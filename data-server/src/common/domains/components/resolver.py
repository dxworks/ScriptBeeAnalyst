"""Component resolution primitives.

Port of legacy ``src/enrichment/components/{mapping,resolver}.py``. The
resolver is plain glue (no Pydantic): given a path, return its component
name following first-match-wins:

1. Explicit :class:`ComponentMapping` entry — longest-prefix wins across
   ``(path_prefix, *extra_paths)``.
2. Top-folder fallback (path before the first '/').
3. ``"(other)"`` synthetic component for separator-less paths when an
   explicit mapping exists but no entry matches.

A :class:`ComponentMapping` is loaded from an optional JSON file; missing
or malformed files fall back to the heuristic-only mode silently. The v2
:class:`ComponentResolverMetric` reads this mapping via
``config.components_mapping_path``.
"""
from __future__ import annotations

import json
import os
from typing import Any, Mapping, Optional

from pydantic import BaseModel, Field


OTHER_COMPONENT = "(other)"


class ComponentSpec(BaseModel):
    """A single (path_prefix + extra_paths) entry in a mapping."""

    path_prefix: str
    extra_paths: list[str] = Field(default_factory=list)


class ComponentMapping(BaseModel):
    """Round-trippable name → :class:`ComponentSpec` map.

    Mirror of the legacy ``ComponentMapping`` with no behaviour change.
    """

    components: dict[str, ComponentSpec] = Field(default_factory=dict)

    def items(self):
        return self.components.items()

    def is_empty(self) -> bool:
        return not self.components


def parse_component_mapping(raw: Optional[Mapping[str, Any]]) -> ComponentMapping:
    """Validate a pre-loaded mapping dict. Never raises.

    The lenient validation rules (drop non-dict specs, require a string
    ``path_prefix``, coerce non-list ``extra_paths`` to empty) match the
    legacy path-based loader so callers feeding the dict path produce
    identical mappings to callers feeding a file path.
    """
    if not isinstance(raw, Mapping):
        return ComponentMapping()
    parsed: dict[str, ComponentSpec] = {}
    for name, spec in raw.items():
        if not isinstance(spec, Mapping):
            continue
        prefix = spec.get("path_prefix")
        if not isinstance(prefix, str) or not prefix:
            continue
        extra = spec.get("extra_paths") or []
        if not isinstance(extra, list):
            extra = []
        parsed[name] = ComponentSpec(
            path_prefix=prefix,
            extra_paths=[p for p in extra if isinstance(p, str)],
        )
    return ComponentMapping(components=parsed)


def load_component_mapping(path: Optional[str]) -> ComponentMapping:
    """Read mapping file or return an empty mapping. Never raises.

    Faithful port of the legacy ``load_component_mapping`` — silent
    fallback on missing / malformed files keeps the metric resilient in
    real-world deployments. Validation is delegated to
    :func:`parse_component_mapping` so the file path and the in-memory
    dict (B2 per-project mapping) share one code path.
    """
    if not path or not os.path.isfile(path):
        return ComponentMapping()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return ComponentMapping()
    if not isinstance(raw, dict):
        return ComponentMapping()
    return parse_component_mapping(raw)


def top_folder_of(file_id: Optional[str]) -> Optional[str]:
    """Top-level folder for a path, or the path itself if no '/'."""
    if not file_id:
        return None
    return file_id.split("/", 1)[0] if "/" in file_id else file_id


class ComponentResolver:
    """Resolves a file path to a component name.

    Faithful port of the legacy
    ``src/enrichment/components/resolver.py:ComponentResolver``; the
    only changes are the Pydantic / typed mapping shapes.
    """

    def __init__(self, mapping: ComponentMapping):
        self._mapping = mapping
        prefixes: list[tuple[str, str]] = []
        for name, spec in mapping.components.items():
            prefixes.append((spec.path_prefix, name))
            for extra in spec.extra_paths:
                prefixes.append((extra, name))
        # Longest-prefix wins — sort once at construction.
        self._prefixes = sorted(prefixes, key=lambda p: -len(p[0]))

    @property
    def is_heuristic(self) -> bool:
        """``True`` if no explicit mapping was loaded (top-folder fallback)."""
        return self._mapping.is_empty()

    def resolve(self, file_id: Optional[str]) -> Optional[str]:
        if not file_id:
            return None
        for prefix, name in self._prefixes:
            if file_id.startswith(prefix):
                return name
        top = top_folder_of(file_id)
        if top is None:
            return None
        if "/" not in file_id and not self._mapping.is_empty():
            return OTHER_COMPONENT
        return top

    def prefix_for(self, name: str) -> str:
        """Canonical ``path_prefix`` for a component name.

        Mapped components return the explicit prefix. Heuristic
        components return the name itself (the top folder). The
        ``(other)`` synthetic component has an empty prefix.
        """
        spec = self._mapping.components.get(name)
        if spec is not None:
            return spec.path_prefix
        if name == OTHER_COMPONENT:
            return ""
        return name


__all__ = [
    "ComponentMapping",
    "ComponentResolver",
    "ComponentSpec",
    "OTHER_COMPONENT",
    "load_component_mapping",
    "parse_component_mapping",
    "top_folder_of",
]
