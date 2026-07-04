"""Thread-safe in-memory storage for typed v2 :class:`Graph` instances.

Chunk 8 rewrite: the same ``get`` / ``set`` / ``delete`` / ``exists`` /
``clear`` / ``get_all_project_ids`` surface as the legacy
``GraphStore``, but the value type is now :class:`Graph` (a typed
Pydantic model with one registry per domain) instead of the legacy
``dict[str, Any]`` payload. This keeps every server endpoint compiling
unchanged while moving the in-memory shape to the v2 model.
"""
from __future__ import annotations

import threading
from typing import Dict, Optional

from src.common.kernel import Graph


class GraphStore:
    """Thread-safe registry of project_id → :class:`Graph` instances.

    One project's typed graph at a time, keyed by the project UUID. Locks
    every public access so concurrent ``/build`` and ``/execute`` calls
    don't see torn writes.
    """

    def __init__(self) -> None:
        self._store: Dict[str, Graph] = {}
        self._lock = threading.Lock()

    def get(self, project_id: str) -> Optional[Graph]:
        """Return the loaded :class:`Graph` for ``project_id`` or ``None``."""
        with self._lock:
            return self._store.get(project_id)

    def set(self, project_id: str, graph: Graph) -> None:
        """Store ``graph`` under ``project_id``, replacing any previous entry."""
        with self._lock:
            self._store[project_id] = graph

    def delete(self, project_id: str) -> bool:
        """Drop the entry for ``project_id``; returns ``True`` if removed."""
        with self._lock:
            if project_id in self._store:
                del self._store[project_id]
                return True
            return False

    def exists(self, project_id: str) -> bool:
        """``True`` iff a :class:`Graph` is currently loaded for ``project_id``."""
        with self._lock:
            return project_id in self._store

    def clear(self) -> None:
        """Drop every entry."""
        with self._lock:
            self._store.clear()

    def get_all_project_ids(self) -> list[str]:
        """Return the list of currently loaded project ids."""
        with self._lock:
            return list(self._store.keys())


#: Module-level singleton — imported by ``src.server`` and the MCP sandbox
#: helpers. One process holds one graph_store; the lock is per-instance.
graph_store = GraphStore()


__all__ = ["GraphStore", "graph_store"]
