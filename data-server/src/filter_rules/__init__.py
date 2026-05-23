"""Project-scoped, agent-authored exclusion rules applied at view time.

See ``filter_files.md`` (architecture plan) and ``extension_1.md`` (the
corrections layer) for the design. The package wires four pieces:

* :mod:`models`     — :class:`RuleDSL` + :class:`FilterRule` Pydantic shapes
* :mod:`repository` — Supabase-backed CRUD over ``project_filter_rules``
* :mod:`engine`     — per-(entity_kind, field) resolvers + ``compute_excluded_ids``
* :mod:`store`      — thread-safe in-memory cache keyed by ``project_id``
* :mod:`views`      — :class:`FilteredSandboxView` wrapping :class:`MCPSandboxView`
* :mod:`router`     — FastAPI router mounted by ``src.server``
"""
from __future__ import annotations

from src.filter_rules.models import FilterRule, Predicate, RuleDSL

__all__ = [
    "FilterRule",
    "Predicate",
    "RuleDSL",
]
