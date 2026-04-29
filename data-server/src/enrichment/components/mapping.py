"""Optional `components.mapping.json` loader.

Schema:
    {
      "<component_name>": {
        "path_prefix": "src/foo/",
        "extra_paths": ["lib/foo-helpers/"]
      },
      ...
    }

`path_prefix` is required, `extra_paths` is optional. Mapping wins over the
top-folder heuristic in the resolver. Missing/invalid files fall back silently
to the heuristic — None-guarded all the way through.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from pydantic import BaseModel, Field

from src.logger import get_logger

LOG = get_logger(__name__)


class ComponentSpec(BaseModel):
    path_prefix: str
    extra_paths: list[str] = Field(default_factory=list)


class ComponentMapping(BaseModel):
    """Round-trippable mapping name -> ComponentSpec."""
    components: dict[str, ComponentSpec] = Field(default_factory=dict)

    def items(self):
        return self.components.items()

    def is_empty(self) -> bool:
        return not self.components


def load_component_mapping(path: Optional[str]) -> ComponentMapping:
    """Read mapping file or return an empty mapping. Never raises."""
    if not path:
        return ComponentMapping()
    if not os.path.isfile(path):
        LOG.info("Components mapping not found at %s; using top-folder heuristic.", path)
        return ComponentMapping()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        LOG.warning("Failed to read components mapping %s (%s); using heuristic.", path, e)
        return ComponentMapping()
    if not isinstance(raw, dict):
        LOG.warning("Components mapping at %s must be a JSON object; got %s.", path, type(raw).__name__)
        return ComponentMapping()
    parsed: dict[str, ComponentSpec] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
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
