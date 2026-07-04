"""Project-scoped, agent-authored exclusion rules applied at view time.

See ``filter_files.md`` (architecture plan) and ``extension_1.md`` (the
corrections layer) for the design. This package's only public surface is
the Pydantic shapes re-exported below; the other submodules
(:mod:`models` / :mod:`engine` / :mod:`repository` / :mod:`store` /
:mod:`views` / :mod:`router`) are imported directly by their callers —
``src.server`` mounts :mod:`router` and reads the singleton from
:mod:`store` plus :class:`~src.filter_rules.views.FilteredSandboxView`.
"""
from __future__ import annotations

from src.filter_rules.models import FilterRule, Predicate, RuleDSL

__all__ = [
    "FilterRule",
    "Predicate",
    "RuleDSL",
]
