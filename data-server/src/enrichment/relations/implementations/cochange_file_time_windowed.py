"""Cochange (file ↔ file, time-windowed) builder — DEFERRED stub.

Port of legacy ``src/enrichment/relations/cochange_file_time_windowed.py``.
The legacy emits cochange pairs where both files were touched in commits
within ``config.time_windowed_cochange_hours`` of each other (rather than
in the same commit). Deferred because the legacy algorithm walks every
ordered commit pair and the v2 port wants a typed temporal index that
Chunk 8 has not yet wired.

See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class CochangeFileTimeWindowedBuilder(RelationBuilder):
    name = "cochange.file_time_windowed"
    relation_kind = "cochange_file_time_windowed"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        raise NotImplementedError(
            "CochangeFileTimeWindowedBuilder port deferred — see Chunk 7 handoff "
            "'Deferred ports' for the temporal-index dependency."
        )


__all__ = ["CochangeFileTimeWindowedBuilder"]
