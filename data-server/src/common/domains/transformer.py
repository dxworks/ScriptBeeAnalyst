"""Shared :class:`Transformer` ABC + :class:`TransformResult` payload.

Per plan §9 step 5, every data source ingested into the v2 graph implements a
single, uniform contract::

    class Transformer(ABC):
        source: ClassVar[SourceKind]

        @abstractmethod
        def transform(self, raw: Any) -> TransformResult: ...

A ``TransformResult`` carries:

* ``project`` — the concrete :class:`Project` subclass instance that owns the
  source-side entities (e.g. a ``GitProject``).
* ``entities`` — bucketed by :class:`EntityKind` so the registry-dispatcher in
  Chunk 8 can route each bucket to the right registry without reflection.

This module lives in ``common/domains/`` so every domain subpackage (and
Chunk 8's processor) can import it without crossing a domain boundary. See
the Chunk 4 handoff for the location decision.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, List, Mapping

from pydantic import BaseModel, ConfigDict, Field

from ..kernel import Entity, EntityKind
from ..people.source import SourceKind
from ..projects import Project


class TransformResult(BaseModel):
    """Bucketed output of a single :class:`Transformer.transform` call.

    Attributes
    ----------
    project:
        The :class:`Project` subclass instance describing the source.
        Concrete projects (``GitProject``, ``JiraProject``, …) must be added
        to the graph's :class:`ProjectRegistry` by the dispatcher; they are
        listed here too so the processor doesn't have to fish them out.
    entities:
        ``EntityKind`` → list of :class:`Entity` instances. Chunk 8 iterates
        each bucket and calls ``graph.registry_for(kind).add(entity)``.
        Each bucket is intentionally ordered so deterministic builds /
        deterministic tests are easy.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    project: Project
    entities: Mapping[EntityKind, List[Entity]] = Field(default_factory=dict)


class Transformer(ABC):
    """Uniform raw → entities contract.

    Concrete subclasses MUST declare :pyattr:`source`. The processor uses it
    to discover which transformer handles a given source kind, and the kernel
    pattern (``Entity.__init_subclass__``) is mirrored here to enforce the
    declaration at class-creation time: missing ``source`` on a concrete
    subclass raises :class:`TypeError` from the class statement itself.

    Intermediate abstract bases opt out by passing ``abstract=True`` in the
    class statement (mirrors ``Entity(Entity, abstract=True)``)::

        class HttpTransformer(Transformer, abstract=True):
            '''Shared scaffolding for any HTTP-fetched source.'''
            ...

        class GitTransformer(HttpTransformer):  # or directly: Transformer
            source: ClassVar[SourceKind] = SourceKind.GIT
            ...

    The raw input type is intentionally :class:`Any` — different sources
    consume different wire formats (``.iglog`` bytes for Git, parsed JSON
    dicts for Jira/GitHub, etc.). Each concrete transformer documents the
    type it accepts.
    """

    source: ClassVar[SourceKind]

    def __init_subclass__(cls, abstract: bool = False, **kwargs: Any) -> None:
        """Validate that every concrete subclass declares ``source``.

        Mirrors the kernel's ``Entity.__init_subclass__`` pattern (Chunk 1
        review optional fix #1). Concrete leaves MUST declare ``source``
        either on the class itself or on a non-Transformer ancestor in
        the MRO before the :class:`Transformer` base. Intermediate
        abstract bases opt out via ``abstract=True``.
        """
        super().__init_subclass__(**kwargs)
        if abstract:
            cls.__transformer_abstract__ = True
            return
        cls.__transformer_abstract__ = False
        if "source" not in cls.__dict__:
            declared = False
            for base in cls.__mro__[1:]:
                if base is Transformer:
                    break
                if "source" in base.__dict__:
                    declared = True
                    break
            if not declared:
                raise TypeError(
                    f"Concrete Transformer subclass {cls.__name__!r} must "
                    f"declare ``source: ClassVar[SourceKind] = "
                    f"SourceKind.<X>``. Pass ``abstract=True`` in the class "
                    f"statement to opt out (intermediate bases)."
                )

    @abstractmethod
    def transform(self, raw: Any) -> TransformResult:
        """Convert a raw source payload to a :class:`TransformResult`.

        Implementations must NOT mutate the graph directly — only return
        entities. The dispatcher (Chunk 8) is the single chokepoint that
        writes them to registries.
        """


__all__ = ["Transformer", "TransformResult"]
