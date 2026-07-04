"""Per-project enrichment-config overrides.

Each row in ``project_config_overrides`` carries a JSON dict that overlays
the global :class:`EnrichmentConfig` defaults at build time. See
``implementation_plan.md`` §2 for the architecture.

This package's only public surface is the Pydantic shapes re-exported
below; the other submodules (:mod:`models` / :mod:`repository` /
:mod:`catalogue` / :mod:`merge` / :mod:`router`) are imported directly by
their callers.
"""
from __future__ import annotations

from src.config_overrides.models import (
    ConfigOverridesPayload,
    ConfigOverridesRow,
)

__all__ = [
    "ConfigOverridesPayload",
    "ConfigOverridesRow",
]
