"""RelationBuilder ABC — placeholder for Chunk 7's ProjectLinker port.

See §6 of ``architectural_changes.md``. Chunk 7 will port the legacy
``ProjectLinker`` and the per-builder modules under
``src/enrichment/relations/*.py`` to this interface. This chunk only
defines the shape so downstream chunks can register against a stable type.

A subclass produces :class:`Relation` entities; Chunk 7's pipeline will
iterate every registered builder, collect their outputs, and route them
into ``graph.relations`` (which deduplicates by canonical id — see
:meth:`Relation.canonical_id`).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Iterable

from .models import Relation

if TYPE_CHECKING:  # forward-only: the Graph type lives in the kernel
    from src.common.kernel import Graph


class RelationBuilder(ABC):
    """Pluggable producer of :class:`Relation` entities.

    Subclass contract::

        class IssueCommitLinker(RelationBuilder):
            name: ClassVar[str] = "issue.commit"
            relation_kind: ClassVar[str] = "issue_commit"

            def build(self, graph: "Graph") -> Iterable[Relation]:
                # walk the graph, yield Relation objects using
                # Relation.canonical_id(...) so duplicates dedup naturally
                ...

    Chunk 7 will add the registration plumbing (probably a
    ``BUILDERS: list[type[RelationBuilder]]`` per-module catalog plus a
    pipeline that instantiates and runs them). This file deliberately
    does NOT include that yet — registration is an enrichment-pipeline
    concern, not a relation-model concern.
    """

    #: Human-readable name. Distinct from ``relation_kind`` — multiple
    #: builders may emit the same relation_kind under different names
    #: (e.g. a "fast path" and a "exhaustive" builder for cochange).
    name: ClassVar[str]

    #: The ``relation_kind`` this builder primarily emits. Used by the
    #: enrichment pipeline to route outputs and by ``list_metrics`` to
    #: describe what the builder produces.
    relation_kind: ClassVar[str]

    @abstractmethod
    def build(self, graph: "Graph") -> Iterable[Relation]:
        """Produce relations from the current graph state.

        Iterable — implementations may yield, or return a list/tuple. The
        pipeline does not assume materialised collections; large builders
        can stream.
        """


__all__ = ["RelationBuilder"]
