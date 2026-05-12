"""Projects — abstract :class:`Project` base + :class:`ProjectRegistry`.

Public API for Chunks 4+ (per-domain projects)::

    from src.common.projects import Project, ProjectRegistry

See §3 of ``architectural_changes.md``.
"""
from __future__ import annotations

from .project import Project, ProjectRegistry

__all__ = ["Project", "ProjectRegistry"]
