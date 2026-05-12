"""Cochange (file ↔ file, shared-devs) — DEFERRED stub.

Port of legacy ``src/enrichment/relations/cochange_file_shared_devs.py``.
The legacy filters cochange edges to pairs whose touching commits share
a developer — the v2 port wants a typed (file, author) intersection index
that Chunk 8 has not yet wired.

See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations_v2 import Relation, RelationBuilder, WindowKind
from src.enrichment.relations_v2.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class CochangeFileSharedDevsBuilder(RelationBuilder):
    name = "cochange.file_shared_devs"
    relation_kind = "cochange_file_shared_devs"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        raise NotImplementedError(
            "CochangeFileSharedDevsBuilder port deferred — see Chunk 7 handoff "
            "'Deferred ports'."
        )


__all__ = ["CochangeFileSharedDevsBuilder"]
