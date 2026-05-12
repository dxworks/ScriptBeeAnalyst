"""External-duplication builder.

Port of legacy ``src/enrichment/relations/duplication_external.py``.
Walks ``graph.duplications`` (the v2
:class:`DuplicationPairRegistry`) and emits a :class:`Relation` for
every pair whose two files do NOT live in the same immediate parent
directory.

Strength = ``token_count`` (the v2 rename of the legacy
``total_block_length``). ``extras['block_count']`` carries the number
of duplicated blocks.

Sibling pairs (same directory) are handled by
:mod:`.duplication_sibling`.
"""
from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class DuplicationExternalBuilder(RelationBuilder):
    name = "duplication.external"
    relation_kind = "duplication_external"
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
            if file_a_ref is None or file_b_ref is None:
                continue
            # File ids ARE paths in v2 (chunk 4); cheap to split by directory.
            if posixpath.dirname(file_a_ref.id) == posixpath.dirname(file_b_ref.id):
                continue  # → sibling builder
            token_count = float(getattr(pair, "token_count", 0))
            block_count = int(getattr(pair, "block_count", 1))
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
                extras={"block_count": block_count},
            )


__all__ = ["DuplicationExternalBuilder"]
