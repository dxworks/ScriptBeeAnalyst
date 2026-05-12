"""GraphStore — set/get/delete/exists/get_all_project_ids over typed Graphs.

Chunk 8: the legacy ``GraphStore`` stored ``dict[str, Any]`` payloads;
the new one stores typed :class:`Graph` instances. The public surface
(set/get/delete/exists/clear/get_all_project_ids) is preserved so
``server.py`` doesn't have to change its call shape.
"""
from __future__ import annotations

import pytest

from src.common.kernel import Graph
from src.graph_store import GraphStore, graph_store


def _graph(project_id: str) -> Graph:
    return Graph(project_id=project_id)


def test_set_get_returns_same_instance():
    store = GraphStore()
    g = _graph("p1")
    store.set("p1", g)
    assert store.get("p1") is g


def test_get_unknown_project_returns_none():
    store = GraphStore()
    assert store.get("ghost") is None


def test_set_replaces_existing_entry():
    store = GraphStore()
    g1 = _graph("p1")
    g2 = _graph("p1")
    store.set("p1", g1)
    store.set("p1", g2)
    assert store.get("p1") is g2


def test_delete_removes_entry_and_reports_true():
    store = GraphStore()
    store.set("p1", _graph("p1"))
    assert store.delete("p1") is True
    assert store.get("p1") is None


def test_delete_unknown_project_reports_false():
    store = GraphStore()
    assert store.delete("ghost") is False


def test_exists_reports_membership():
    store = GraphStore()
    assert store.exists("p1") is False
    store.set("p1", _graph("p1"))
    assert store.exists("p1") is True
    store.delete("p1")
    assert store.exists("p1") is False


def test_clear_removes_every_entry():
    store = GraphStore()
    for i in range(3):
        store.set(f"p{i}", _graph(f"p{i}"))
    assert len(store.get_all_project_ids()) == 3
    store.clear()
    assert store.get_all_project_ids() == []


def test_get_all_project_ids_returns_list_of_keys():
    store = GraphStore()
    for i in range(3):
        store.set(f"p{i}", _graph(f"p{i}"))
    ids = sorted(store.get_all_project_ids())
    assert ids == ["p0", "p1", "p2"]


def test_module_level_singleton_is_a_graph_store():
    """``graph_store`` is the imported singleton; ``server.py`` reads it."""
    assert isinstance(graph_store, GraphStore)


def test_typed_graph_round_trips_through_store():
    """The store accepts a real typed :class:`Graph` and gives it back
    unchanged (no copying / no validation pass).
    """
    store = GraphStore()
    g = _graph("typed")
    g.commits  # touch to force default-init
    store.set("typed", g)

    retrieved = store.get("typed")
    assert retrieved is g
    # The typed registry fields survive intact.
    assert retrieved.project_id == "typed"
    assert len(retrieved.commits) == 0
    assert len(retrieved.relations) == 0
