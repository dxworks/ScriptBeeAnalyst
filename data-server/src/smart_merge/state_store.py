"""Per-project in-memory smart-merge state.

Holds the live :class:`UnifiedUser` list + the cached
:class:`SimilaritiesGraph` + the last-served suggestions map for every
loaded project. Replaces the ``graph_data["users"]`` /
``graph_data["smart_merge_base_graph"]`` /
``graph_data["smart_merge_last_suggestions"]`` slots the Phase-1
``graph_data: dict`` global carried.

Each project's state lives behind a module-level singleton accessed
through :func:`state_for` (lazy-created on first read). Mutation is
protected by a per-instance lock so concurrent endpoint calls don't see
torn writes.

Persistence: nothing on disk lives here. The :class:`UnifiedUser`
records are persisted into Supabase by the existing
:class:`SupabaseSmartMergeRepository`; this store only holds the
replayed copy for fast endpoint reads.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.common.people.unified import UnifiedUser
from src.smart_merge.types import SimilaritiesGraph, Suggestion


@dataclass
class ProjectSmartMergeState:
    """Mutable per-project smart-merge state.

    Attributes mirror the slots the legacy ``graph_data`` dict carried:

    * :pyattr:`users` — the replayed-from-Supabase :class:`UnifiedUser`
      list, mutated by apply / delete endpoints.
    * :pyattr:`base_graph` — cached pairwise similarity graph; lets the
      suggestions endpoint skip the O(n^2) rebuild on repeat reads.
    * :pyattr:`last_suggestions` — keyed by ``suggestion_id`` so the
      identities-pagination endpoint can serve a stable list.
    """

    users: List[UnifiedUser] = field(default_factory=list)
    base_graph: Optional[SimilaritiesGraph] = None
    last_suggestions: Dict[str, Suggestion] = field(default_factory=dict)

    def invalidate_cache(self) -> None:
        """Drop cached base graph + last suggestions after any mutation.

        Matches the Phase-1 ``_invalidate_smart_merge_cache`` semantics.
        """
        self.base_graph = None
        self.last_suggestions = {}


class SmartMergeStateStore:
    """Thread-safe project_id → :class:`ProjectSmartMergeState` map."""

    def __init__(self) -> None:
        self._store: Dict[str, ProjectSmartMergeState] = {}
        self._lock = threading.Lock()

    def get(self, project_id: str) -> ProjectSmartMergeState:
        """Return (or lazily create) the state for ``project_id``."""
        with self._lock:
            state = self._store.get(project_id)
            if state is None:
                state = ProjectSmartMergeState()
                self._store[project_id] = state
            return state

    def reset(self, project_id: str) -> ProjectSmartMergeState:
        """Replace the entry for ``project_id`` with a fresh state."""
        with self._lock:
            state = ProjectSmartMergeState()
            self._store[project_id] = state
            return state

    def delete(self, project_id: str) -> bool:
        """Drop the state for ``project_id``; returns ``True`` if removed."""
        with self._lock:
            return self._store.pop(project_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


#: Module-level singleton imported by ``src.server``.
smart_merge_state_store = SmartMergeStateStore()


__all__ = [
    "ProjectSmartMergeState",
    "SmartMergeStateStore",
    "smart_merge_state_store",
]
