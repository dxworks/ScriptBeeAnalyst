"""RelationRegistry — five reverse indexes for cross-entity traversal.

See §6 of ``architectural_changes.md``. The five indexes correspond exactly
to the most common query shapes from the legacy code:

* "all relations from X"      → ``by_source[X]``
* "all relations into X"      → ``by_target[X]``
* "all relations of a kind"   → ``by_kind[kind]``
* "kind+window slice"         → ``by_kind_window[(kind, window)]``
* "all relations between A↔B" → ``by_pair[(A, B)]``

Each convenience method on :class:`RelationRegistry` wraps one index so
callers don't need to know index names.
"""
from __future__ import annotations

from typing import Tuple

from src.common.kernel import EntityRef, IndexSpec, Registry

from .models import Relation, WindowKind


class RelationRegistry(Registry[Relation, str]):
    """Reverse-indexed registry of :class:`Relation` entities.

    Indexes (all multi=True — multiple relations may share any one key):

    * ``by_source``      — keyed on ``rel.source`` (an :class:`EntityRef`).
    * ``by_target``      — keyed on ``rel.target`` (an :class:`EntityRef`).
    * ``by_kind``        — keyed on ``rel.relation_kind`` (str).
    * ``by_kind_window`` — keyed on the tuple ``(relation_kind, window)``.
    * ``by_pair``        — keyed on the tuple ``(source, target)``.

    Convenience methods (:meth:`for_source`, :meth:`for_target`,
    :meth:`of_kind`, :meth:`of_kind_in_window`, :meth:`between`) wrap each
    index for caller ergonomics.
    """

    indexes = [
        IndexSpec(name="by_source",      key_fn=lambda r: r.source),
        IndexSpec(name="by_target",      key_fn=lambda r: r.target),
        IndexSpec(name="by_kind",        key_fn=lambda r: r.relation_kind),
        IndexSpec(
            name="by_kind_window",
            key_fn=lambda r: (r.relation_kind, r.window),
        ),
        IndexSpec(name="by_pair",        key_fn=lambda r: (r.source, r.target)),
    ]

    def get_id(self, entity: Relation) -> str:
        return entity.id

    # ------------------------------------------------------------------
    # Convenience methods — one per index. All return tuples (snapshot
    # semantics, never live views) per the kernel multi-index contract.
    # ------------------------------------------------------------------
    def for_source(self, source: EntityRef) -> Tuple[Relation, ...]:
        """Every :class:`Relation` whose ``source`` is ``source``."""
        return self.by_source[source]  # type: ignore[attr-defined,no-any-return]

    def for_target(self, target: EntityRef) -> Tuple[Relation, ...]:
        """Every :class:`Relation` whose ``target`` is ``target``."""
        return self.by_target[target]  # type: ignore[attr-defined,no-any-return]

    def of_kind(self, relation_kind: str) -> Tuple[Relation, ...]:
        """Every :class:`Relation` whose ``relation_kind`` matches."""
        return self.by_kind[relation_kind]  # type: ignore[attr-defined,no-any-return]

    def of_kind_in_window(
        self, relation_kind: str, window: WindowKind | str
    ) -> Tuple[Relation, ...]:
        """Every :class:`Relation` matching ``(relation_kind, window)``.

        Accepts ``window`` as either a :class:`WindowKind` enum member OR
        its bare string value (e.g. ``"recent"``). The string form is
        coerced to the enum so the index lookup matches.

        Matches :meth:`Relation.canonical_id`'s lenient contract — both
        accept the same shapes so callers can pass either consistently.
        Passing a string that is not a valid :class:`WindowKind` value
        raises :class:`ValueError` (Python's StrEnum behaviour).
        """
        win = WindowKind(window) if isinstance(window, str) and not isinstance(window, WindowKind) else window
        return self.by_kind_window[(relation_kind, win)]  # type: ignore[attr-defined,no-any-return]

    def between(
        self, source: EntityRef, target: EntityRef
    ) -> Tuple[Relation, ...]:
        """Every :class:`Relation` matching ``(source, target)``."""
        return self.by_pair[(source, target)]  # type: ignore[attr-defined,no-any-return]


__all__ = ["RelationRegistry"]
