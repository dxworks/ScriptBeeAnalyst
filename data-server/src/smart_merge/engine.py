"""
Author Smart Merge Engine.
Computes merge suggestions by building a similarity graph, pruning conflicts,
and extracting connected components as suggestion clusters.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set
from uuid import uuid4

from src.common.unified_author import SourceIdentity, UnifiedUser
from src.logger import get_logger
from src.smart_merge.graph import (
    clone_graph,
    connected_components,
    edge_between,
    remove_edge,
    similar_neighbours,
    upsert_node,
    upsert_undirected_edge,
)
from src.smart_merge.recommendation import compute_default_name_email
from src.smart_merge.repository import SmartMergeRepository
from src.smart_merge.token_similarity import compute_best_similarity
from src.smart_merge.types import (
    SimilaritiesGraph,
    SimilarityType,
    Suggestion,
)

LOG = get_logger(__name__)

# Maximum strength possible for confidence scoring
MAX_STRENGTH = 10


class AuthorSmartMergeEngine:
    def __init__(self, repo: SmartMergeRepository) -> None:
        self.repo = repo

    def compute_suggestions(
        self,
        identities: List[SourceIdentity],
        project_id: str,
        existing_users: Optional[List[UnifiedUser]] = None,
        activity_counts: Optional[Dict[str, int]] = None,
    ) -> List[Suggestion]:
        """
        Compute merge suggestions from a list of source identities.

        Pipeline:
        1. Build base similarities graph (O(n^2) pairwise)
        2. Clone for mutable run
        3. Remove persisted rejected pairs
        4. Add SAME_AUTHOR links for identities already in the same UnifiedUser
        5. Prune DIFFERENT-link conflicts
        6. Extract connected components
        7. Filter clusters with >= 2 distinct identities not yet in the same user
        8. Generate suggestions

        Args:
            identities: All source identities from the loaded graph.
            project_id: The project UUID.
            existing_users: Currently merged UnifiedUsers (for SAME_AUTHOR links).
            activity_counts: Optional map of identity.key -> activity count.
        """
        if len(identities) < 2:
            return []

        LOG.info(f"Building similarities graph for {len(identities)} identities")
        base_graph = self._build_base_graph(identities)

        graph = clone_graph(base_graph)
        self._remove_rejected(project_id, graph)
        self._add_same_author_links(existing_users or [], graph)
        self._prune_different_conflicts(graph)

        return self._build_suggestions(
            graph, identities, existing_users or [], activity_counts or {}
        )

    def _build_base_graph(self, identities: List[SourceIdentity]) -> SimilaritiesGraph:
        """Build pairwise similarity graph from all identities."""
        graph = SimilaritiesGraph()

        for i, first in enumerate(identities):
            first_key = upsert_node(graph, first)

            for second in identities[i + 1:]:
                second_key = upsert_node(graph, second)

                sim = compute_best_similarity(
                    name_a=first.name,
                    email_a=first.email,
                    login_a=first.login,
                    name_b=second.name,
                    email_b=second.email,
                    login_b=second.login,
                )

                upsert_undirected_edge(
                    graph, first_key, second_key, sim.type, sim.strength
                )

        edge_count = sum(len(row) for row in graph.adj.values()) // 2
        LOG.info(f"Built graph: {len(graph.nodes)} nodes, {edge_count} edges")
        return graph

    def _remove_rejected(self, project_id: str, graph: SimilaritiesGraph) -> None:
        """Remove edges for previously rejected similarity pairs."""
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
        self, existing_users: List[UnifiedUser], graph: SimilaritiesGraph
    ) -> None:
        """Add SAME_AUTHOR links between identities already in the same UnifiedUser."""
        for user in existing_users:
            keys = [i.key for i in user.identities if i.key in graph.nodes]
            for i, a in enumerate(keys):
                for b in keys[i + 1:]:
                    remove_edge(graph, a, b)
                    upsert_undirected_edge(
                        graph, a, b, SimilarityType.SAME_AUTHOR, MAX_STRENGTH
                    )

    def _prune_different_conflicts(self, graph: SimilaritiesGraph) -> None:
        """
        Prune weak transitive links broken by DIFFERENT edges.

        For each DIFFERENT edge A-B:
        - Find common SIMILAR neighbours C
        - Remove A-C or B-C if their strength <= strength(A-B)
        - Remove the DIFFERENT edge itself
        """
        different_edges = []
        for a, row in graph.adj.items():
            for b, e in row.items():
                if a < b and e.type == SimilarityType.DIFFERENT:
                    different_edges.append((a, b, e.strength))

        pruned = 0
        for a, b, strength in different_edges:
            sim_a = similar_neighbours(graph, a)
            sim_b = similar_neighbours(graph, b)
            common = sim_a.intersection(sim_b)

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
        existing_users: List[UnifiedUser],
        activity_counts: Dict[str, int],
    ) -> List[Suggestion]:
        """Build suggestion list from connected components."""
        # Map identity keys to their parent UnifiedUser (if any)
        key_to_user: Dict[str, str] = {}
        for user in existing_users:
            for identity in user.identities:
                key_to_user[identity.key] = user.id

        # Build identity lookup
        key_to_identity: Dict[str, SourceIdentity] = {i.key: i for i in all_identities}

        suggestions: List[Suggestion] = []
        for component in connected_components(graph):
            # Collect distinct identities in this cluster
            cluster_identities: List[SourceIdentity] = []
            for key in component:
                identity = key_to_identity.get(key) or graph.nodes.get(key)
                if identity:
                    cluster_identities.append(identity)

            if len(cluster_identities) < 2:
                continue

            # Skip if all identities already belong to the same UnifiedUser
            user_ids = {key_to_user.get(i.key) for i in cluster_identities}
            user_ids.discard(None)
            if len(user_ids) == 1 and None not in {key_to_user.get(i.key) for i in cluster_identities}:
                continue

            # Compute confidence from max edge strength in cluster
            max_strength = 0
            for i, a_key in enumerate(component):
                for b_key in component[i + 1:]:
                    e = edge_between(graph, a_key, b_key)
                    if e and e.strength > max_strength:
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

        # Sort by confidence descending (show best matches first)
        suggestions.sort(key=lambda s: s.confidence, reverse=True)
        LOG.info(f"Generated {len(suggestions)} merge suggestions")
        return suggestions
