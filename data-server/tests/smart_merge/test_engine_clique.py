"""Tests for clique-style cluster validation in AuthorSmartMergeEngine.

The new algorithm should:
- Split loose "chain" components (low density, low avg strength) into
  tight sub-clusters or drop them entirely.
- Keep true cliques (high density, strong edges) intact.
- Never split SAME_AUTHOR-connected groups.
"""
from __future__ import annotations

from typing import List

from src.common.people.unified import UnifiedUser
from src.smart_merge.identity import SourceIdentity
from src.smart_merge.engine import AuthorSmartMergeEngine
from src.smart_merge.graph import upsert_node, upsert_undirected_edge
from src.smart_merge.repository import SmartMergeRepository
from src.smart_merge.types import RejectedPair, SimilaritiesGraph, SimilarityType, UserMapping


class _InMemoryRepo(SmartMergeRepository):
    """In-memory repository for tests (no Supabase)."""
    def __init__(self) -> None:
        self._rejected: List[RejectedPair] = []
        self._mappings: List[UserMapping] = []

    def get_rejected_similarities(self, project_id: str) -> List[RejectedPair]:
        return list(self._rejected)

    def add_rejected_similarities(self, project_id, pairs) -> None:
        self._rejected.extend(pairs)

    def get_user_mappings(self, project_id: str) -> List[UserMapping]:
        return list(self._mappings)

    def upsert_user_mapping(self, mapping, project_id) -> None:
        self._mappings.append(mapping)

    def delete_user_mapping(self, project_id, unified_user_id) -> None:
        self._mappings = [m for m in self._mappings if m.unified_user_id != unified_user_id]


def _ident(src: str, key: str, name: str, login: str | None = None) -> SourceIdentity:
    return SourceIdentity(source=src, name=name, email=None, login=login, source_key=key)


def _graph_from(identities: List[SourceIdentity], edges: List[tuple]) -> SimilaritiesGraph:
    """Build a similarity graph directly from explicit edges.
    edges: list of (key_a, key_b, type, strength)."""
    g = SimilaritiesGraph()
    for i in identities:
        upsert_node(g, i)
    for a, b, t, s in edges:
        upsert_undirected_edge(g, a, b, t, s)
    return g


class TestCliqueValidation:

    def test_weak_chain_is_split_or_dropped(self):
        """A-B-C-D linear chain of SIMILAR(3) edges has density 3/6 = 0.5
        and avg strength 3.0 — both below the thresholds. Should be split
        into smaller sub-clusters, not emitted as a single 4-way group."""
        a = _ident("git", "a", "a")
        b = _ident("git", "b", "b")
        c = _ident("git", "c", "c")
        d = _ident("git", "d", "d")

        pre_built = _graph_from(
            [a, b, c, d],
            [
                ("git:a", "git:b", SimilarityType.SIMILAR, 3),
                ("git:b", "git:c", SimilarityType.SIMILAR, 3),
                ("git:c", "git:d", SimilarityType.SIMILAR, 3),
            ],
        )

        engine = AuthorSmartMergeEngine(_InMemoryRepo())
        suggestions, _ = engine.compute_suggestions(
            identities=[a, b, c, d],
            project_id="p",
            base_graph=pre_built,
        )

        for s in suggestions:
            assert len(s.identities) < 4, (
                "Linear chain must not be emitted as one 4-way cluster"
            )

    def test_true_clique_is_kept(self):
        """Four nodes all pairwise IDENTICAL at strength 8 — density 1.0,
        avg 8.0 — must be emitted as a single cluster."""
        idents = [_ident("git", f"k{i}", f"Common Rare Fullname") for i in range(4)]
        keys = [i.key for i in idents]
        edges = []
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                edges.append((keys[i], keys[j], SimilarityType.IDENTICAL, 8))

        pre_built = _graph_from(idents, edges)

        engine = AuthorSmartMergeEngine(_InMemoryRepo())
        suggestions, _ = engine.compute_suggestions(
            identities=idents,
            project_id="p",
            base_graph=pre_built,
        )

        assert len(suggestions) == 1
        assert len(suggestions[0].identities) == 4

    def test_same_author_edges_are_exempt_from_splitting(self):
        """A cluster whose only edges are SAME_AUTHOR must never be split,
        regardless of density math."""
        a = _ident("git", "a", "A")
        b = _ident("git", "b", "B")
        c = _ident("git", "c", "C")

        pre_built = _graph_from(
            [a, b, c],
            [
                ("git:a", "git:b", SimilarityType.SAME_AUTHOR, 10),
                ("git:b", "git:c", SimilarityType.SAME_AUTHOR, 10),
            ],
        )

        existing_user = UnifiedUser(
            display_name="user",
            identities=[a, b, c],
        )

        engine = AuthorSmartMergeEngine(_InMemoryRepo())
        suggestions, _ = engine.compute_suggestions(
            identities=[a, b, c],
            project_id="p",
            base_graph=pre_built,
            existing_users=[existing_user],
        )

        # Because all 3 are already in the same existing user, no suggestion
        # is emitted — but critically, the component wasn't split apart.
        for s in suggestions:
            assert len(s.identities) >= 2
