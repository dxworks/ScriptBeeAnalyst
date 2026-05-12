"""File-name similarity builder — DEFERRED stub.

Port of legacy ``src/enrichment/relations/similarity_file_names.py``. The
legacy computes ``difflib.SequenceMatcher`` ratios over file basenames
with extension-bucket pre-filter + per-file top-N cap. The port is
straightforward but high-volume; deferred until Chunk 8 wires the file
registry on the v2 host and we can confirm the index shape.

See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations_v2 import Relation, RelationBuilder, WindowKind
from src.enrichment.relations_v2.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class SimilarityFileNamesBuilder(RelationBuilder):
    name = "similarity.file_names"
    relation_kind = "similarity_file_names"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        raise NotImplementedError(
            "SimilarityFileNamesBuilder port deferred — Chunk 8 file-registry "
            "wiring not yet finalised. See handoff."
        )


__all__ = ["SimilarityFileNamesBuilder"]
