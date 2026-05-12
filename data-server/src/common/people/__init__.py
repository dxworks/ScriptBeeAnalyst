"""People — Account base, UnifiedUser, SourceKind.

Public API for Chunks 4/5/6 (per-domain account subclasses) and Chunks
7/9 (smart-merge + relation builder)::

    from src.common.people import (
        Account, SourceKind,
        UnifiedUser, UnifiedUserRegistry,
    )

See §2 of ``architectural_changes.md`` and the Chunk 2 handoff
(``plan/task_4/handoffs/chunk_02_people_projects.md``) for the worked
examples + decoupling rationale.
"""
from __future__ import annotations

from .account import Account
from .source import SourceKind
from .unified import UnifiedUser, UnifiedUserRegistry

__all__ = [
    "Account",
    "SourceKind",
    "UnifiedUser",
    "UnifiedUserRegistry",
]
