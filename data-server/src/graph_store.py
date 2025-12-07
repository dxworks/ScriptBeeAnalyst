"""
Thread-safe in-memory storage for project graphs.
Stores graphs by project_id for isolation.
"""

import threading
from typing import Dict, Optional, Any


class GraphStore:
    """
    Thread-safe dictionary for storing project graphs.
    Each project_id maps to a dict with 'git', 'jira', 'github' keys.
    """

    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, project_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve graph data for a project.

        Args:
            project_id: UUID of the project

        Returns:
            Dict with 'git', 'jira', 'github' keys, or None if not found
        """
        with self._lock:
            return self._store.get(project_id)

    def set(self, project_id: str, graph_data: Dict[str, Any]) -> None:
        """
        Store graph data for a project.

        Args:
            project_id: UUID of the project
            graph_data: Dict with 'git', 'jira', 'github' keys
        """
        with self._lock:
            self._store[project_id] = graph_data

    def delete(self, project_id: str) -> bool:
        """
        Remove graph data for a project.

        Args:
            project_id: UUID of the project

        Returns:
            True if project was found and deleted, False otherwise
        """
        with self._lock:
            if project_id in self._store:
                del self._store[project_id]
                return True
            return False

    def exists(self, project_id: str) -> bool:
        """
        Check if graph data exists for a project.

        Args:
            project_id: UUID of the project

        Returns:
            True if graph exists, False otherwise
        """
        with self._lock:
            return project_id in self._store

    def clear(self) -> None:
        """Clear all stored graphs."""
        with self._lock:
            self._store.clear()

    def get_all_project_ids(self) -> list[str]:
        """
        Get list of all project IDs currently loaded.

        Returns:
            List of project_id strings
        """
        with self._lock:
            return list(self._store.keys())


# Global graph store instance
graph_store = GraphStore()
