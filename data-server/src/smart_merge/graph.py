"""
Similarity graph operations: build, clone, mutate, and extract connected components.
"""
from __future__ import annotations

from copy import deepcopy
from typing import List, Optional, Set

from src.common.unified_author import SourceIdentity
from src.smart_merge.types import Edge, SimilaritiesGraph, SimilarityType


def clone_graph(graph: SimilaritiesGraph) -> SimilaritiesGraph:
    return deepcopy(graph)


def upsert_node(graph: SimilaritiesGraph, identity: SourceIdentity) -> str:
    """Add a node if it doesn't exist. Returns the node key."""
    k = identity.key
    graph.nodes.setdefault(k, identity)
    graph.adj.setdefault(k, {})
    return k


def upsert_undirected_edge(
    graph: SimilaritiesGraph,
    a: str,
    b: str,
    type_: SimilarityType,
    strength: int,
) -> None:
    if a == b or strength <= 0:
        return
    edge = Edge(a=a, b=b, type=type_, strength=strength)
    graph.adj.setdefault(a, {})[b] = edge
    graph.adj.setdefault(b, {})[a] = edge


def remove_edge(graph: SimilaritiesGraph, a: str, b: str) -> None:
    graph.adj.get(a, {}).pop(b, None)
    graph.adj.get(b, {}).pop(a, None)


def edge_between(graph: SimilaritiesGraph, a: str, b: str) -> Optional[Edge]:
    return graph.adj.get(a, {}).get(b)


def similar_neighbours(graph: SimilaritiesGraph, node: str) -> Set[str]:
    return {
        n
        for n, e in graph.adj.get(node, {}).items()
        if e.type == SimilarityType.SIMILAR
    }


def connected_components(graph: SimilaritiesGraph) -> List[List[str]]:
    """Find connected components via DFS."""
    seen: set[str] = set()
    out: List[List[str]] = []

    for start in graph.nodes:
        if start in seen:
            continue
        stack = [start]
        comp: List[str] = []
        seen.add(start)
        while stack:
            n = stack.pop()
            comp.append(n)
            for nxt in graph.adj.get(n, {}):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        out.append(comp)

    return out
