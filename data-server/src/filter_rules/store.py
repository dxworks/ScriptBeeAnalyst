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
from src.filter_rules.engine import compute_excluded_ids, compute_rule_match_counts
from src.filter_rules.models import FilterRule
from src.filter_rules.repository import FilterRuleRepository
from src.graph_store import graph_store
from src.logger import get_logger

LOG = get_logger(__name__)


@dataclass
class ProjectFilterState:
    """Cached rules + per-kind excluded ids + per-rule match counts for one project."""

    rules: List[FilterRule] = field(default_factory=list)
    excluded_ids: Dict[EntityKind, Set[str]] = field(default_factory=dict)
    # rule.id -> match count. Empty when no graph is loaded for the project.
    match_counts: Dict[str, int] = field(default_factory=dict)


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
        if graph is not None:
            excluded = compute_excluded_ids(graph, rules)
            match_counts = compute_rule_match_counts(graph, rules)
        else:
            excluded = {}
            match_counts = {}
        state = ProjectFilterState(
            rules=rules, excluded_ids=excluded, match_counts=match_counts
        )
        with self._lock:
            self._store[project_id] = state
        LOG.info(
            f"filter_rules refresh: project={project_id} "
            f"rules={len(rules)} "
            f"excluded_kinds={sorted(k.value for k in excluded)}"
        )
        return state

    def list(self, project_id: str) -> List[FilterRule]:
        return list(self.state(project_id).rules)

    def state(self, project_id: str) -> ProjectFilterState:
        """Return the cached state, refreshing if cold."""
        with self._lock:
            cached = self._store.get(project_id)
        if cached is not None:
            return cached
        return self.refresh(project_id)

    def excluded_ids_for(
        self, project_id: str
    ) -> Dict[EntityKind, Set[str]]:
        """Excluded ids for ``project_id`` from the cached refresh result.

        Computed once per :meth:`refresh` (called by ``/load``, ``/build``,
        and every mutating router endpoint), so this read is O(rules) —
        no registry walk on the ``/execute`` hot path.
        """
        with self._lock:
            state = self._store.get(project_id)
        if state is None:
            state = self.refresh(project_id)
        return {k: set(v) for k, v in state.excluded_ids.items()}

    def delete(self, project_id: str) -> None:
        with self._lock:
            self._store.pop(project_id, None)


filter_rule_store = FilterRuleStore()


__all__ = ["FilterRuleStore", "ProjectFilterState", "filter_rule_store"]
