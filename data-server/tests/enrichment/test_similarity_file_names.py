"""Tests for :class:`SimilarityFileNamesBuilder` (Chunk 12).

Subset port of the legacy ``tests/enrichment/test_relations.py``
similarity coverage, plus per-builder unit tests built on the v2
conftest helpers.

Strength is the :class:`difflib.SequenceMatcher` ratio over basenames;
defaults are ``min_score=0.85`` / ``max_pairs_per_file=50`` (no
extension filter), matching :class:`EnrichmentConfig`.
"""
from __future__ import annotations

from src.enrichment.relations import BUILDERS, WindowKind
from src.enrichment.relations.implementations.similarity_file_names import (
    SimilarityFileNamesBuilder,
)

from tests.enrichment.conftest import build_v2_graph, make_file


# ----------------------------------------------------------------------
# Catalog wiring
# ----------------------------------------------------------------------
def test_builder_is_registered():
    assert "similarity.file_names" in BUILDERS
    assert BUILDERS.get("similarity.file_names") is SimilarityFileNamesBuilder


def test_builder_metadata():
    assert SimilarityFileNamesBuilder.relation_kind == "similarity.file-file.names"
    assert SimilarityFileNamesBuilder.window == WindowKind.LIFETIME


# ----------------------------------------------------------------------
# Emission behaviour
# ----------------------------------------------------------------------
def test_empty_graph_emits_no_relations():
    g, _ = build_v2_graph("sfn-empty")
    rels = list(SimilarityFileNamesBuilder().build(g))
    assert rels == []


def test_single_file_emits_no_relations():
    g, p = build_v2_graph("sfn-one")
    g.files.add(make_file("src/lonely.py", p.ref()))
    rels = list(SimilarityFileNamesBuilder().build(g))
    assert rels == []


def test_dissimilar_basenames_below_threshold_yield_no_edge():
    g, p = build_v2_graph("sfn-dissimilar")
    g.files.add(make_file("src/alpha.py", p.ref()))
    g.files.add(make_file("src/omega.py", p.ref()))
    rels = list(SimilarityFileNamesBuilder().build(g))
    # 0.4 ratio — well below the 0.85 default.
    assert rels == []


def test_similar_basenames_emit_one_lifetime_edge():
    g, p = build_v2_graph("sfn-similar")
    g.files.add(make_file("src/foo.py", p.ref()))
    g.files.add(make_file("src/foooo.py", p.ref()))
    rels = list(SimilarityFileNamesBuilder().build(g))
    assert len(rels) == 1
    rel = rels[0]
    assert rel.relation_kind == "similarity.file-file.names"
    assert rel.window == WindowKind.LIFETIME
    assert rel.strength > 0.85
    # Canonical ordering: source/target sorted by (kind, id).
    endpoints = {rel.source.id, rel.target.id}
    assert endpoints == {"src/foo.py", "src/foooo.py"}


def test_basename_compared_not_full_path():
    """Two files in different dirs with the same basename should match."""
    g, p = build_v2_graph("sfn-basename")
    g.files.add(make_file("src/util.py", p.ref()))
    g.files.add(make_file("tests/util.py", p.ref()))
    rels = list(SimilarityFileNamesBuilder().build(g))
    assert len(rels) == 1
    assert rels[0].strength == 1.0  # identical basenames


def test_extension_filter_restricts_buckets():
    """With ``ext_filter=True`` files of different extensions never pair."""
    g, p = build_v2_graph("sfn-ext")
    g.files.add(make_file("src/foo.py", p.ref()))
    g.files.add(make_file("src/foo.js", p.ref()))  # identical basename stem

    class _Host:
        """Pydantic Graph forbids extras; wrap the Graph in a stub host
        that also carries the ``config`` attr the builder reads."""
        def __init__(self, graph):
            self._graph = graph

        def __getattr__(self, item):
            return getattr(self._graph, item)

    class _Cfg:
        name_similarity_min_score = 0.5
        name_similarity_extension_filter = True
        name_similarity_max_pairs_per_file = 50

    host = _Host(g)
    host.config = _Cfg()
    rels = list(SimilarityFileNamesBuilder().build(host))
    # Same basename stem but different extensions → different buckets → no edge.
    assert rels == []


def test_no_self_pairs():
    """A single file's basename comparing to itself must not yield an edge."""
    g, p = build_v2_graph("sfn-self")
    g.files.add(make_file("src/foo.py", p.ref()))
    g.files.add(make_file("src/foo.py.bak", p.ref()))  # not the same id
    rels = list(SimilarityFileNamesBuilder().build(g))
    # Each Relation, if emitted, must have distinct endpoints.
    for r in rels:
        assert r.source != r.target


def test_canonical_id_is_deterministic():
    """Two runs over the same input must produce the same Relation ids."""
    g1, p1 = build_v2_graph("sfn-det1")
    g1.files.add(make_file("src/foo.py", p1.ref()))
    g1.files.add(make_file("src/foooo.py", p1.ref()))
    ids_1 = sorted(r.id for r in SimilarityFileNamesBuilder().build(g1))

    g2, p2 = build_v2_graph("sfn-det2")
    # Same paths under a different project — ids depend only on file refs.
    g2.files.add(make_file("src/foooo.py", p2.ref()))
    g2.files.add(make_file("src/foo.py", p2.ref()))
    ids_2 = sorted(r.id for r in SimilarityFileNamesBuilder().build(g2))

    assert ids_1 == ids_2
