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

# Pre-load the kernel package before any people submodule begins importing.
# Otherwise the smart_merge entry-point (server.py:29) triggers the cycle:
#   smart_merge -> people.unified -> people/__init__ -> account.py
#   -> kernel/__init__ (eager Graph load) -> domains/git/models.py
#   -> ...people.account (still mid-load; Account class not yet defined).
# Importing kernel up-front lets graph.py drain the domain-models chain
# BEFORE the people package starts loading its own submodules. Pytest
# happens to hit this order naturally (test files import kernel first),
# which masked the bug. See P3.A note in kernel/__init__.py.
import src.common.kernel  # noqa: F401

from .account import Account
from .source import SourceKind
from .unified import UnifiedUser, UnifiedUserRegistry

__all__ = [
    "Account",
    "SourceKind",
    "UnifiedUser",
    "UnifiedUserRegistry",
]
