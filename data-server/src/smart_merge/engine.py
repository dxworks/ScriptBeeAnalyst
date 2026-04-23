"""
Author Smart Merge Engine.
Computes merge suggestions by building a similarity graph, pruning conflicts,
and extracting connected components as suggestion clusters.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple
from uuid import uuid4

from src.common.unified_author import SourceIdentity
from src.logger import get_logger
from src.smart_merge.graph import (
    clone_graph,
    component_avg_strength,
    component_density,
    connected_components,
    edge_between,
    remove_edge,
    similar_neighbours,
    subgraph_edges,
    upsert_node,
    upsert_undirected_edge,
    weakest_non_same_author_edge,
)
from src.smart_merge.recommendation import compute_default_name_email
from src.smart_merge.repository import SmartMergeRepository
from src.smart_merge.token_similarity import (
    compute_best_similarity,
    email_address_without_domain,
)
from src.smart_merge.types import (
    SimilaritiesGraph,
    SimilarityType,
    Suggestion,
)

LOG = get_logger(__name__)

MAX_STRENGTH = 10
NAME_BLOCK_PREFIX_LEN = 3
LOGIN_BLOCK_PREFIX_LEN = 3
MIN_CLUSTER_DENSITY = 0.6
MIN_CLUSTER_AVG_STRENGTH = 4.0
MAX_CLUSTER_SIZE = 200

SPLIT_RE = re.compile(r"\s|\.|-")


class AuthorSmartMergeEngine:
    def __init__(self, repo: SmartMergeRepository) -> None:
        self.repo = repo

    def compute_suggestions(
        self,
        identities: List[SourceIdentity],
        project_id: str,
        existing_users: Optional[List] = None,
        activity_counts: Optional[Dict[str, int]] = None,
        base_graph: Optional[SimilaritiesGraph] = None,
    ) -> Tuple[List[Suggestion], SimilaritiesGraph]:
        """
        Compute merge suggestions.

        Returns (suggestions, base_graph). The caller can cache base_graph and
        pass it back on subsequent calls (same identity set) to skip the O(n^2)
        rebuild.

        Pipeline:
        1. Build or reuse base similarities graph (blocking-reduced pairwise)
        2. Clone for mutable run
        3. Remove persisted rejected pairs
        4. Add SAME_AUTHOR links for identities already in the same UnifiedUser
        5. Prune DIFFERENT-link conflicts
        6. Extract connected components
        7. Split blobs that fail density + avg-strength checks into tight sub-clusters
        8. Emit suggestions (skip clusters > MAX_CLUSTER_SIZE with a warning)
        """
        if len(identities) < 2:
            return [], base_graph or SimilaritiesGraph()

        if base_graph is None or not self._base_graph_matches(base_graph, identities):
            LOG.info(f"Building similarities graph for {len(identities)} identities")
            base_graph = self._build_base_graph(identities)
        else:
            LOG.info("Reusing cached base similarities graph")

        graph = clone_graph(base_graph)
        self._remove_rejected(project_id, graph)
        self._add_same_author_links(existing_users or [], graph)
        self._prune_different_conflicts(graph)

        suggestions = self._build_suggestions(
            graph, identities, existing_users or [], activity_counts or {}
        )
        return suggestions, base_graph

    @staticmethod
    def _base_graph_matches(
        base_graph: SimilaritiesGraph, identities: List[SourceIdentity]
    ) -> bool:
        """Cheap validity check: same identity key set means cache is still good."""
        return set(base_graph.nodes.keys()) == {i.key for i in identities}

    def _build_base_graph(self, identities: List[SourceIdentity]) -> SimilaritiesGraph:
        """Build pairwise similarity graph using blocking to skip clearly unrelated pairs."""
        graph = SimilaritiesGraph()
        for identity in identities:
            upsert_node(graph, identity)

        candidate_pairs = self._candidate_pairs(identities)
        LOG.info(
            f"Blocking reduced candidate pairs to {len(candidate_pairs)} "
            f"(from max {len(identities) * (len(identities) - 1) // 2})"
        )

        edges_added = 0
        for a, b in candidate_pairs:
            sim = compute_best_similarity(
                name_a=a.name,
                email_a=a.email,
                login_a=a.login,
                name_b=b.name,
                email_b=b.email,
                login_b=b.login,
            )
            if sim.strength <= 0:
                continue
            upsert_undirected_edge(graph, a.key, b.key, sim.type, sim.strength)
            edges_added += 1

        LOG.info(f"Built graph: {len(graph.nodes)} nodes, {edges_added} edges")
        return graph

    @staticmethod
    def _tokenize(s: str) -> List[str]:
        return [t for t in SPLIT_RE.split((s or "").lower().strip()) if t]

    def _candidate_pairs(
        self, identities: List[SourceIdentity]
    ) -> List[Tuple[SourceIdentity, SourceIdentity]]:
        """Index identities into blocks, return unique unordered pairs sharing a block.

        Blocks:
        - name-token prefix (first NAME_BLOCK_PREFIX_LEN chars of each name token)
        - login prefix (first LOGIN_BLOCK_PREFIX_LEN chars of the login)
        - email local-part prefix (first LOGIN_BLOCK_PREFIX_LEN chars)
        """
        by_name: Dict[str, List[int]] = defaultdict(list)
        by_login: Dict[str, List[int]] = defaultdict(list)
        by_email: Dict[str, List[int]] = defaultdict(list)

        for idx, ident in enumerate(identities):
            for tok in self._tokenize(ident.name):
                if len(tok) >= NAME_BLOCK_PREFIX_LEN:
                    by_name[tok[:NAME_BLOCK_PREFIX_LEN]].append(idx)
                else:
                    by_name[tok].append(idx)

            if ident.login:
                key = ident.login.lower().strip()[:LOGIN_BLOCK_PREFIX_LEN]
                if key:
                    by_login[key].append(idx)

            if ident.email:
                local = email_address_without_domain(ident.email).lower()
                key = local[:LOGIN_BLOCK_PREFIX_LEN]
                if key:
                    by_email[key].append(idx)

        pair_set: Set[Tuple[int, int]] = set()
        for bucket_map in (by_name, by_login, by_email):
            for bucket in bucket_map.values():
                if len(bucket) < 2:
                    continue
                for i in range(len(bucket)):
                    for j in range(i + 1, len(bucket)):
                        a, b = bucket[i], bucket[j]
                        if a != b:
                            pair_set.add((a, b) if a < b else (b, a))

        return [(identities[i], identities[j]) for i, j in pair_set]

    def _remove_rejected(self, project_id: str, graph: SimilaritiesGraph) -> None:
        rejected = self.repo.get_rejected_similarities(project_id)
        removed = 0
        for pair in rejected:
            key_a = f"{pair.first_source}:{pair.first_source_key}"
            key_b = f"{pair.second_source}:{pair.second_source_key}"
            if edge_between(graph, key_a, key_b) is not None:
                remove_edge(graph, key_a, key_b)
                removed += 1
        if removed:
            LOG.info(f"Removed {removed} rejected similarity edges")

    def _add_same_author_links(
        self, existing_users: List, graph: SimilaritiesGraph
    ) -> None:
        for user in existing_users:
            keys = [i.key for i in user.identities if i.key in graph.nodes]
            for i, a in enumerate(keys):
                for b in keys[i + 1:]:
                    remove_edge(graph, a, b)
                    upsert_undirected_edge(
                        graph, a, b, SimilarityType.SAME_AUTHOR, MAX_STRENGTH
                    )

    def _prune_different_conflicts(self, graph: SimilaritiesGraph) -> None:
        """For each DIFFERENT edge A-B: drop weak A-C / B-C that go through
        shared SIMILAR neighbours, then drop the DIFFERENT edge itself."""
        different_edges = []
        for a, row in graph.adj.items():
            for b, e in row.items():
                if a < b and e.type == SimilarityType.DIFFERENT:
                    different_edges.append((a, b, e.strength))

        pruned = 0
        for a, b, strength in different_edges:
            common = similar_neighbours(graph, a).intersection(similar_neighbours(graph, b))
            for c in common:
                e1 = edge_between(graph, a, c)
                e2 = edge_between(graph, b, c)
                if e1 and e1.strength <= strength:
                    remove_edge(graph, a, c)
                    pruned += 1
                if e2 and e2.strength <= strength:
                    remove_edge(graph, b, c)
                    pruned += 1
            remove_edge(graph, a, b)

        if pruned:
            LOG.info(f"Pruned {pruned} weak edges via DIFFERENT conflict resolution")

    def _build_suggestions(
        self,
        graph: SimilaritiesGraph,
        all_identities: List[SourceIdentity],
        existing_users: List,
        activity_counts: Dict[str, int],
    ) -> List[Suggestion]:
        """Turn the post-pruning graph into suggestions, splitting loose blobs."""
        key_to_user: Dict[str, str] = {}
        for user in existing_users:
            for identity in user.identities:
                key_to_user[identity.key] = user.id

        key_to_identity: Dict[str, SourceIdentity] = {i.key: i for i in all_identities}

        components = connected_components(graph)
        tight_clusters: List[List[str]] = []
        for component in components:
            if len(component) < 2:
                continue
            tight_clusters.extend(self._extract_tight_clusters(graph, component))

        suggestions: List[Suggestion] = []
        dropped_oversize = 0
        for cluster in tight_clusters:
            cluster_identities = [
                key_to_identity.get(k) or graph.nodes.get(k)
                for k in cluster
                if (key_to_identity.get(k) or graph.nodes.get(k)) is not None
            ]
            if len(cluster_identities) < 2:
                continue

            if len(cluster_identities) > MAX_CLUSTER_SIZE:
                dropped_oversize += 1
                LOG.warning(
                    f"Dropping oversize cluster of {len(cluster_identities)} "
                    f"identities (> {MAX_CLUSTER_SIZE})"
                )
                continue

            user_ids = {key_to_user.get(i.key) for i in cluster_identities}
            if None not in user_ids and len(user_ids) == 1:
                continue

            max_strength = 0
            for e in subgraph_edges(graph, cluster):
                if e.strength > max_strength:
                    max_strength = e.strength
            confidence = min(max_strength / MAX_STRENGTH, 1.0) if MAX_STRENGTH > 0 else 0.0

            default_name, default_email = compute_default_name_email(
                cluster_identities, activity_counts
            )

            suggestions.append(Suggestion(
                suggestion_id=str(uuid4()),
                default_name=default_name,
                default_email=default_email,
                confidence=confidence,
                identities=cluster_identities,
            ))

        suggestions.sort(key=lambda s: ((s.default_name or "").casefold(), (s.default_email or "").casefold()))
        if dropped_oversize:
            LOG.warning(f"Dropped {dropped_oversize} oversize clusters")
        LOG.info(f"Generated {len(suggestions)} merge suggestions")
        return suggestions

    def _extract_tight_clusters(
        self, graph: SimilaritiesGraph, component: List[str]
    ) -> List[List[str]]:
        """Iteratively split a connected component by removing the weakest
        non-SAME_AUTHOR edge until every sub-component passes the density and
        avg-strength checks (or is trivially small).

        Operates on a component-scoped adjacency map to avoid cloning the full
        graph per component.
        """
        if len(component) < 2:
            return [component]

        # Size-2 components need a single edge above thresholds to pass —
        # with the post-prune graph, a size-2 cluster always has a single
        # strong edge (weak ones got filtered in the builder), so shortcut.
        if len(component) == 2:
            return [component]

        # Local adjacency restricted to this component's nodes.
        node_set = set(component)
        local_adj: Dict[str, Dict[str, int]] = {n: {} for n in node_set}
        local_type: Dict[Tuple[str, str], SimilarityType] = {}
        for a in node_set:
            for b, e in graph.adj.get(a, {}).items():
                if b in node_set and a < b:
                    local_adj[a][b] = e.strength
                    local_adj[b][a] = e.strength
                    local_type[(a, b)] = e.type

        def edges_of(nodes: Iterable[str]) -> List[Tuple[str, str, int, SimilarityType]]:
            ns = set(nodes)
            seen: Set[Tuple[str, str]] = set()
            out: List[Tuple[str, str, int, SimilarityType]] = []
            for a in ns:
                for b, s in local_adj.get(a, {}).items():
                    if b in ns:
                        key = (a, b) if a < b else (b, a)
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append((key[0], key[1], s, local_type[key]))
            return out

        def passes(nodes: List[str]) -> bool:
            if len(nodes) < 2:
                return True
            edges = edges_of(nodes)
            max_edges = len(nodes) * (len(nodes) - 1) // 2
            density = len(edges) / max_edges if max_edges else 1.0
            avg = (sum(s for _, _, s, _ in edges) / len(edges)) if edges else 0.0
            return density >= MIN_CLUSTER_DENSITY and avg >= MIN_CLUSTER_AVG_STRENGTH

        def weakest(nodes: List[str]) -> Optional[Tuple[str, str]]:
            rank = {
                SimilarityType.SIMILAR: 0,
                SimilarityType.IDENTICAL: 1,
            }
            candidates = [
                (a, b, s, t) for a, b, s, t in edges_of(nodes)
                if t != SimilarityType.SAME_AUTHOR
            ]
            if not candidates:
                return None
            a, b, *_ = min(candidates, key=lambda e: (e[2], rank.get(e[3], 99)))
            return (a, b)

        def components_within(nodes: List[str]) -> List[List[str]]:
            ns = set(nodes)
            seen: Set[str] = set()
            out: List[List[str]] = []
            for start in ns:
                if start in seen:
                    continue
                stack = [start]
                comp: List[str] = []
                seen.add(start)
                while stack:
                    n = stack.pop()
                    comp.append(n)
                    for nxt in local_adj.get(n, {}):
                        if nxt in ns and nxt not in seen:
                            seen.add(nxt)
                            stack.append(nxt)
                out.append(comp)
            return out

        queue: List[List[str]] = [component]
        tight: List[List[str]] = []

        while queue:
            nodes = queue.pop()
            if passes(nodes):
                tight.append(nodes)
                continue
            w = weakest(nodes)
            if w is None:
                tight.append(nodes)
                continue
            a, b = w
            local_adj[a].pop(b, None)
            local_adj[b].pop(a, None)
            local_type.pop((a, b) if a < b else (b, a), None)
            queue.extend(components_within(nodes))

        return [c for c in tight if len(c) >= 2]
