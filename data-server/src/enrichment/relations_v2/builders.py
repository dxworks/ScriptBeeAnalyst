"""RelationBuilder ABC + ``BuilderRegistry`` catalog.

See §6 of ``architectural_changes.md``. Chunk 7 ports the legacy
``ProjectLinker`` and the per-builder modules under
``src/enrichment/relations/*.py`` to this interface.

A subclass produces :class:`Relation` entities; the Chunk 7 pipeline
(``src/enrichment/v2_pipeline.py``) iterates every registered builder,
collects their outputs, and routes them into ``graph.relations`` (which
deduplicates by canonical id — see :meth:`Relation.canonical_id`).

Decorator-style registration mirrors the :class:`MetricRegistry` shape::

    @BUILDERS.register
    class CoauthorBuilder(RelationBuilder):
        name = "coauthor"
        relation_kind = "coauthor"

        def build(self, graph): ...

The module-level singleton :data:`BUILDERS` is the canonical catalog.
Tests that need isolation can instantiate :class:`BuilderRegistry`
directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Iterable, Iterator

from .models import Relation, WindowKind

if TYPE_CHECKING:  # forward-only — keeps cycles broken
    from src.common.kernel import Graph


class RelationBuilder(ABC):
    """Pluggable producer of :class:`Relation` entities.

    Subclass contract::

        @BUILDERS.register
        class IssueCommitLinker(RelationBuilder):
            name: ClassVar[str] = "issue.commit"
            relation_kind: ClassVar[str] = "issue_commit"
            window: ClassVar[WindowKind] = WindowKind.LIFETIME

            def build(self, graph) -> Iterable[Relation]:
                # walk the graph, yield Relation objects using
                # Relation.canonical_id(...) so duplicates dedup naturally
                ...

    The Chunk-7 pipeline (``src/enrichment/v2_pipeline.py``) iterates
    every builder in :data:`BUILDERS` and routes outputs to
    ``graph.relations``.
    """

    #: Human-readable name. Distinct from ``relation_kind`` — multiple
    #: builders may emit the same relation_kind under different names
    #: (e.g. a "fast path" and a "exhaustive" builder for cochange).
    name: ClassVar[str]

    #: The ``relation_kind`` this builder primarily emits. Used by the
    #: enrichment pipeline to route outputs and by ``list_metrics`` to
    #: describe what the builder produces.
    relation_kind: ClassVar[str]

    #: Default window the builder emits. Builders that emit multiple
    #: windows (e.g. lifetime+recent) override per-emission; this is the
    #: catalog hint for ``list_metrics``-style introspection.
    window: ClassVar[WindowKind] = WindowKind.LIFETIME

    @abstractmethod
    def build(self, graph: "Graph") -> Iterable[Relation]:
        """Produce relations from the current graph state.

        Iterable — implementations may yield, or return a list/tuple. The
        pipeline does not assume materialised collections; large builders
        can stream.

        **Purity contract.** A ``build`` implementation MUST be a pure
        function of ``graph``: it must NOT mutate the graph or any of its
        registries. The pipeline owns registry mutations and routes the
        yielded :class:`Relation` instances into ``graph.relations``.
        Two builders emitting the same logical relation collapse
        naturally on ``Registry.add(...)`` because the canonical id is
        the dedup key.
        """


# ----------------------------------------------------------------------
# BuilderRegistry — plain catalog (mirrors MetricRegistry shape)
# ----------------------------------------------------------------------
class BuilderRegistry:
    """Catalog of :class:`RelationBuilder` subclasses, keyed by ``name``.

    Decorator-friendly — :meth:`register` returns the class so it can be
    used as ``@BUILDERS.register``. Not picklable — the catalog is a
    global, code-only artefact rebuilt on every import. Same shape as
    :class:`src.enrichment.metrics.MetricRegistry`.
    """

    __slots__ = ("_by_name",)

    def __init__(self) -> None:
        self._by_name: dict[str, type[RelationBuilder]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(
        self, builder_cls: type[RelationBuilder]
    ) -> type[RelationBuilder]:
        """Add ``builder_cls`` to the catalog. Returns it (decorator-style).

        Raises :class:`ValueError` if a different class is already
        registered under the same ``name``. Re-registering the *same*
        class (e.g. due to a module re-import) is a no-op.
        """
        if not isinstance(builder_cls, type):  # defensive — caller error
            raise TypeError(
                f"BuilderRegistry.register expects a class, got "
                f"{type(builder_cls).__name__}"
            )
        if not issubclass(builder_cls, RelationBuilder):
            raise TypeError(
                f"{builder_cls.__name__} must subclass RelationBuilder to register"
            )
        name = getattr(builder_cls, "name", None)
        if not isinstance(name, str) or not name:
            raise TypeError(
                f"{builder_cls.__name__} must declare a non-empty "
                f"``name: ClassVar[str]`` to register"
            )
        existing = self._by_name.get(name)
        if existing is not None and existing is not builder_cls:
            raise ValueError(
                f"BuilderRegistry already has a different class registered "
                f"under name {name!r}: {existing.__name__} (new: "
                f"{builder_cls.__name__})"
            )
        self._by_name[name] = builder_cls
        return builder_cls

    # ------------------------------------------------------------------
    # Lookup / iteration
    # ------------------------------------------------------------------
    def get(self, name: str) -> type[RelationBuilder]:
        """Return the builder class registered under ``name``.

        Raises :class:`KeyError` if not found.
        """
        try:
            return self._by_name[name]
        except KeyError as e:
            raise KeyError(
                f"No builder registered under name {name!r}. Registered: "
                f"{sorted(self._by_name)}"
            ) from e

    def all(self) -> list[type[RelationBuilder]]:
        """Snapshot list of every registered builder class."""
        return list(self._by_name.values())

    def names(self) -> list[str]:
        """Snapshot list of registered builder names (insertion order)."""
        return list(self._by_name.keys())

    def __iter__(self) -> Iterator[type[RelationBuilder]]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    # ------------------------------------------------------------------
    # Test / cleanup
    # ------------------------------------------------------------------
    def unregister(self, name: str) -> type[RelationBuilder]:
        """Drop the builder registered under ``name`` and return its class."""
        try:
            return self._by_name.pop(name)
        except KeyError as e:
            raise KeyError(
                f"No builder registered under name {name!r}. Registered: "
                f"{sorted(self._by_name)}"
            ) from e

    def clear(self) -> None:
        """Drop every registered builder. Mainly for test isolation."""
        self._by_name.clear()


#: The module-level singleton — Chunk 7 implementations decorate against this.
BUILDERS = BuilderRegistry()


__all__ = ["BUILDERS", "BuilderRegistry", "RelationBuilder"]
