"""Cochange (author ↔ author, time-windowed) — DEFERRED stub.

Port of legacy ``src/enrichment/relations/cochange_author_time_windowed.py``.
Depends on the temporal-index used by ``cochange.file_time_windowed``.

See handoff §"Deferred ports".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.enrichment.relations_v2 import Relation, RelationBuilder, WindowKind
from src.enrichment.relations_v2.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class CochangeAuthorTimeWindowedBuilder(RelationBuilder):
    name = "cochange.author_time_windowed"
    relation_kind = "cochange_author_time_windowed"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        raise NotImplementedError(
            "CochangeAuthorTimeWindowedBuilder port deferred — depends on "
            "temporal index. See handoff."
        )


__all__ = ["CochangeAuthorTimeWindowedBuilder"]
