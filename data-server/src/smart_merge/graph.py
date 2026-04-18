"""
Similarity graph operations: build, clone, mutate, and extract connected components.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Iterable, List, Optional, Set, Tuple

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


def subgraph_edges(graph: SimilaritiesGraph, nodes: Iterable[str]) -> List[Edge]:
    """Return distinct edges where both endpoints are in nodes."""
    node_set = set(nodes)
    seen: set[Tuple[str, str]] = set()
    edges: List[Edge] = []
    for a in node_set:
        for b, e in graph.adj.get(a, {}).items():
            if b in node_set:
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(e)
    return edges


def component_density(graph: SimilaritiesGraph, nodes: List[str]) -> float:
    """Edge density of a component: edges / max-possible-undirected-edges.
    Returns 1.0 for components of size <= 1 (no edges possible)."""
    n = len(nodes)
    if n < 2:
        return 1.0
    max_edges = n * (n - 1) // 2
    return len(subgraph_edges(graph, nodes)) / max_edges


def component_avg_strength(graph: SimilaritiesGraph, nodes: List[str]) -> float:
    """Average edge strength in the component. 0.0 if no edges."""
    edges = subgraph_edges(graph, nodes)
    if not edges:
        return 0.0
    return sum(e.strength for e in edges) / len(edges)


def weakest_non_same_author_edge(
    graph: SimilaritiesGraph, nodes: List[str]
) -> Optional[Edge]:
    """Find the lowest-strength edge in the component that isn't SAME_AUTHOR.
    Ties broken by (type rank DIFFERENT<SIMILAR<IDENTICAL<SAME_AUTHOR) so we
    prefer dropping SIMILAR over IDENTICAL at equal strength.
    Returns None if every edge is SAME_AUTHOR."""
    rank = {
        SimilarityType.SIMILAR: 0,
        SimilarityType.IDENTICAL: 1,
        SimilarityType.SAME_AUTHOR: 2,
    }
    candidates = [e for e in subgraph_edges(graph, nodes)
                  if e.type != SimilarityType.SAME_AUTHOR]
    if not candidates:
        return None
    return min(candidates, key=lambda e: (e.strength, rank.get(e.type, 99)))
