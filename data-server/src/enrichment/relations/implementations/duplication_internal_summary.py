"""Per-file internal-duplication summary builder.

Port of legacy
``src/enrichment/relations/duplication_internal_summary.py``. The legacy
data model carried ``Duplication.internal_by_file: dict[str, int]``; the
v2 model collapses internal duplication into a self-pair
:class:`DuplicationPair` (``file_a_ref == file_b_ref``) with the legacy
scalar as ``token_count`` — see the Chunk-6 handoff.

This builder walks ``graph.duplications`` and emits a degenerate
self-loop :class:`Relation` per file with non-zero internal duplication.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class DuplicationInternalSummaryBuilder(RelationBuilder):
    name = "duplication.internal_summary"
    relation_kind = "duplication_internal"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        duplications = getattr(graph, "duplications", None)
        if duplications is None:
            return

        try:
            pairs = list(duplications)
        except TypeError:
            return

        for pair in pairs:
            file_a_ref = getattr(pair, "file_a_ref", None)
            file_b_ref = getattr(pair, "file_b_ref", None)
            if file_a_ref is None or file_a_ref != file_b_ref:
                continue  # we only care about self-pairs here
            token_count = float(getattr(pair, "token_count", 0))
            if token_count <= 0:
                continue
            rid = Relation.canonical_id(
                file_a_ref, file_b_ref, self.relation_kind, WindowKind.LIFETIME
            )
            yield Relation(
                id=rid,
                source=file_a_ref,
                target=file_b_ref,
                relation_kind=self.relation_kind,
                window=WindowKind.LIFETIME,
                strength=token_count,
            )


__all__ = ["DuplicationInternalSummaryBuilder"]
