"""Thread-safe in-memory cache of filter rules + excluded-id sets.

Mirrors the structural pattern of :mod:`src.graph_store`: a per-instance
lock guards a ``project_id -> ProjectFilterState`` dict; mutations clear
the cached excluded-id sets so the next reader pays the recompute. The
v1 implementation deliberately recomputes on every :meth:`refresh` — it's
O(R + E) where R is the rule count and E the entity count of the matched
kinds, comfortably under a millisecond on the project sizes in scope.

The store is the only consumer of :func:`src.filter_rules.engine.compute_excluded_ids`
during normal operation. The POST/DELETE router handlers call
:meth:`refresh` after writing to Supabase so the next ``/execute`` sees
the new world.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from src.common.kernel import EntityKind
from src.common.kernel.graph import Graph
from src.filter_rules.engine import compute_excluded_ids
from src.filter_rules.models import FilterRule
from src.filter_rules.repository import FilterRuleRepository
from src.graph_store import graph_store
from src.logger import get_logger

LOG = get_logger(__name__)


@dataclass
class ProjectFilterState:
    """Cached rules + per-kind excluded ids for one project."""

    rules: List[FilterRule] = field(default_factory=list)
    excluded_ids: Dict[EntityKind, Set[str]] = field(default_factory=dict)


class FilterRuleStore:
    """Thread-safe project_id -> :class:`ProjectFilterState` map."""

    def __init__(
        self, repository: Optional[FilterRuleRepository] = None
    ) -> None:
        self._repository = repository or FilterRuleRepository()
        self._store: Dict[str, ProjectFilterState] = {}
        self._lock = threading.Lock()

    def refresh(self, project_id: str) -> ProjectFilterState:
        """Reload from Supabase and recompute excluded ids against the loaded graph."""
        rules = self._repository.list_for_project(project_id)
        graph = graph_store.get(project_id)
        excluded: Dict[EntityKind, Set[str]] = (
            compute_excluded_ids(graph, rules) if graph is not None else {}
        )
        state = ProjectFilterState(rules=rules, excluded_ids=excluded)
        with self._lock:
            self._store[project_id] = state
        LOG.info(
            f"filter_rules refresh: project={project_id} "
            f"rules={len(rules)} "
            f"excluded_kinds={sorted(k.value for k in excluded)}"
        )
        return state

    def list(self, project_id: str) -> List[FilterRule]:
        with self._lock:
            state = self._store.get(project_id)
        if state is None:
            state = self.refresh(project_id)
        return list(state.rules)

    def excluded_ids_for(
        self, project_id: str, graph: Optional[Graph] = None
    ) -> Dict[EntityKind, Set[str]]:
        """Excluded ids for ``project_id`` against ``graph`` (or the loaded graph).

        When ``graph`` is provided AND differs from the graph the cache was
        computed against, we recompute on the fly — the cache is still
        keyed by ``project_id`` so the next /load triggers a refresh()
        and resets it.
        """
        with self._lock:
            state = self._store.get(project_id)
        if state is None:
            state = self.refresh(project_id)
        if graph is None:
            return {k: set(v) for k, v in state.excluded_ids.items()}
        recomputed = compute_excluded_ids(graph, state.rules)
        return recomputed

    def delete(self, project_id: str) -> None:
        with self._lock:
            self._store.pop(project_id, None)


filter_rule_store = FilterRuleStore()


__all__ = ["FilterRuleStore", "ProjectFilterState", "filter_rule_store"]
