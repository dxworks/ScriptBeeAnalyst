"""Pydantic shapes for the per-project enrichment-config overrides.

The override row is a single JSONB blob keyed by ``EnrichmentConfig``
field name. The payload is open-ended (~80 candidate knobs); fine-grained
per-field validation lives in :mod:`src.config_overrides.catalogue` and
the PUT-endpoint validator. These models only enforce the wire envelope.

The catalogue / router shapes (``CatalogueField``, ``CatalogueResponse``)
land in the catalogue commit alongside the introspection code that
populates them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConfigOverridesPayload(BaseModel):
    """PUT /projects/{id}/config/overrides body.

    The whole dict is replaced on every save (no patch semantics). An
    empty ``overrides`` clears all overrides for the project — effective
    config falls back to :data:`DEFAULT_CONFIG`.
    """

    model_config = ConfigDict(extra="forbid")

    overrides: Dict[str, Any] = Field(default_factory=dict)


class ConfigOverridesRow(BaseModel):
    """In-memory mirror of one ``project_config_overrides`` row.

    ``updated_at`` is ``None`` when no row exists yet for the project —
    the repository returns an empty-dict shape so callers don't need to
    branch on ``is None``.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str
    overrides: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[datetime] = None


__all__ = [
    "ConfigOverridesPayload",
    "ConfigOverridesRow",
]
