"""Relation entity + ``WindowKind`` + ``RelationExtra`` typed union.

See §6 of ``architectural_changes.md``. A :class:`Relation` is a first-class
graph entity carrying ``(source, target, relation_kind, window, strength,
extras)``. The :class:`RelationRegistry` declares five reverse indexes so
queries the legacy code performed via full scans
(``commit.issues``, ``Enrichments.relations``…) are O(1) lookups.

Canonical IDs
-------------

The plan does not specify a Relation id format. We provide
:func:`Relation.canonical_id` (deterministic across runs) so:

* duplicate-by-canonical-id collapses naturally on ``registry.add(...)``
  (two builders producing the same logical relation overwrite each other
  in-place — the deterministic id is the dedup key),
* relations can be addressed by id from the MCP sandbox without leaking
  the index structure.

Window kinds — closed enum
--------------------------

Today's relation builders use only ``"lifetime"`` and ``"recent"`` (verified
by grep against ``src/enrichment/relations/``). The plan §1.1 names the enum
:class:`WindowKind` and lists four members; we ship all four — the
fixed-window members (``LAST_30_DAYS`` / ``LAST_90_DAYS``) are reserved for
the metric port in Chunks 7+. See the handoff for the audit.

Typed extras
------------

``Relation.extras`` is a typed mapping. The shape is **structurally identical**
to ``EvidenceValue`` on :class:`~src.enrichment.tags.base.Trait`, but is
declared independently here so each surface can diverge without ripple
edits. We intentionally do not ``from ..tags.base import EvidenceValue``.
"""
from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from typing_extensions import TypeAliasType

from src.common.kernel import Entity, EntityKind, EntityRef


class WindowKind(StrEnum):
    """Closed set of time-window discriminators on a :class:`Relation`.

    Members marked ``# legacy`` are emitted by ``src/enrichment/relations/``
    today (verified by grep at chunk-3 time). The fixed-window members are
    forward-looking; the metric port (Chunks 7+) will start emitting them.
    """

    LIFETIME       = "lifetime"      # legacy — emitted everywhere
    RECENT         = "recent"        # legacy — co-change builders
    LAST_30_DAYS   = "last_30_days"  # forward (planned)
    LAST_90_DAYS   = "last_90_days"  # forward (planned)


# ----------------------------------------------------------------------
# RelationExtra — typed recursive union (no ``Any``). Same shape as
# ``EvidenceValue`` on traits, declared independently so the two surfaces
# can diverge later without breaking each other. See the Chunk-3 handoff
# "Design choices that diverge from architectural_changes.md".
#
# Pydantic v2 requires ``TypeAliasType`` (PEP 695) for recursive aliases;
# a plain ``X = A | list["X"] | ...`` triggers infinite schema recursion.
# ----------------------------------------------------------------------
RelationExtra = TypeAliasType(
    "RelationExtra",
    str
    | int
    | float
    | bool
    | EntityRef
    | list["RelationExtra"]
    | dict[str, "RelationExtra"],
)


def _canonical_id(
    source: EntityRef,
    target: EntityRef,
    relation_kind: str,
    window: WindowKind | str,
) -> str:
    """Deterministic id format for a :class:`Relation`.

    Shape::

        {relation_kind}:{window}:{source.kind}/{source.id}->{target.kind}/{target.id}

    Two builders emitting the same logical relation collide naturally on
    ``registry.add(...)`` because the resulting ids match. Stable across
    Python runs / pickle cycles — strings only, no hashing.

    ``window`` may be either a :class:`WindowKind` enum member or its bare
    string value (e.g. ``"recent"``). Strings are coerced via
    ``WindowKind(...)`` so an unknown value raises :class:`ValueError` at
    id-construction time rather than silently producing an id that no
    :meth:`RelationRegistry.of_kind_in_window` lookup will ever match.
    Same lenient-but-validated contract as
    :meth:`RelationRegistry.of_kind_in_window` — single source of truth
    across the two surfaces.
    """
    win_enum = window if isinstance(window, WindowKind) else WindowKind(window)
    return (
        f"{relation_kind}:{win_enum.value}:"
        f"{source.kind.value}/{source.id}"
        f"->{target.kind.value}/{target.id}"
    )


class Relation(Entity):
    """A first-class graph relation between two entities.

    Replaces the legacy ``commit.issues``-style list fields scattered on
    entities AND the ``Enrichments.relations`` bag. Adding metadata to a
    relation as a whole (the legacy P4) now means adding an entry to
    ``extras``.

    Construction
    ------------

    Builders should set ``id`` to :func:`canonical_id`'s output so duplicate
    emissions collapse naturally::

        rel = Relation(
            id=Relation.canonical_id(src, tgt, "cochange", WindowKind.RECENT),
            source=src, target=tgt,
            relation_kind="cochange",
            window=WindowKind.RECENT,
            strength=0.42,
            extras={"basis": "coupling"},
        )
        graph.relations.add(rel)
    """

    kind: ClassVar[EntityKind] = EntityKind.RELATION

    source: EntityRef
    target: EntityRef
    relation_kind: str
    window: WindowKind = WindowKind.LIFETIME
    strength: float
    extras: dict[str, RelationExtra] = {}

    @staticmethod
    def canonical_id(
        source: EntityRef,
        target: EntityRef,
        relation_kind: str,
        window: WindowKind | str = WindowKind.LIFETIME,
    ) -> str:
        """Deterministic id for ``(source, target, relation_kind, window)``.

        See :func:`_canonical_id` for the format. Use this when emitting
        relations from a builder so duplicates dedup naturally::

            rid = Relation.canonical_id(src.ref(), tgt.ref(), "issue_commit")
            graph.relations.add(Relation(id=rid, source=..., target=..., ...))
        """
        return _canonical_id(source, target, relation_kind, window)


__all__ = ["Relation", "RelationExtra", "WindowKind"]
