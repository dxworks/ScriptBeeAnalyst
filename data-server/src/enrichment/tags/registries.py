"""Trait + Classifier registries with reverse indexes.

See §5.2 of ``architectural_changes.md``. The two registries make every
"which entities carry trait X?" / "which classifier is on target Y?"
question an O(1) index lookup (vs. the legacy ``Enrichments`` full scans
this refactor replaces — see plan P3).
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from src.common.kernel import EntityRef, IndexSpec, Registry

from .base import Classifier, Trait, TraitFamily


# ----------------------------------------------------------------------
# Trait registry
# ----------------------------------------------------------------------
class TraitRegistry(Registry[Trait, str]):
    """Reverse-indexed registry of :class:`Trait` entities.

    Indexes (all multi=True):

    * ``by_target`` — keyed on ``trait.target`` (an :class:`EntityRef`).
    * ``by_family`` — keyed on ``trait.family`` (a :class:`TraitFamily`).
    * ``by_name``   — keyed on ``trait.name``.

    Convenience methods :meth:`for_target` and :meth:`of_name` wrap those
    indexes so callers don't need to remember names.
    """

    indexes = [
        IndexSpec(name="by_target", key_fn=lambda t: t.target),
        IndexSpec(name="by_family", key_fn=lambda t: t.family),
        IndexSpec(name="by_name",   key_fn=lambda t: t.name),
    ]

    def get_id(self, entity: Trait) -> str:
        return entity.id

    # ---- convenience ----
    def for_target(self, target: EntityRef) -> Tuple[Trait, ...]:
        """Every :class:`Trait` whose ``target`` is ``target``."""
        return self.by_target[target]  # type: ignore[attr-defined,no-any-return]

    def of_name(self, name: str) -> Tuple[Trait, ...]:
        """Every :class:`Trait` whose ``name`` matches ``name``."""
        return self.by_name[name]  # type: ignore[attr-defined,no-any-return]

    def of_family(self, family: TraitFamily) -> Tuple[Trait, ...]:
        """Every :class:`Trait` in a given family.

        Not in the plan-listed minimum surface, but the index already exists
        — exposing it for parity with :meth:`of_name`. Cheaper than asking
        callers to remember the attribute name. The MCP sandbox in Chunk 9
        will probably bind both.
        """
        return self.by_family[family]  # type: ignore[attr-defined,no-any-return]


# ----------------------------------------------------------------------
# Classifier registry
# ----------------------------------------------------------------------
class ClassifierRegistry(Registry[Classifier, str]):
    """Reverse-indexed registry of :class:`Classifier` entities.

    Indexes (all multi=True):

    * ``by_target``    — keyed on ``classifier.target`` (:class:`EntityRef`).
    * ``by_dimension`` — keyed on the **tuple** ``(classifier.dimension,)``
                         (per plan §5.2 — tuple key, even with one element,
                         keeps the index shape consistent across both
                         classifier-dimension reads).
    * ``by_dim_value`` — keyed on the tuple
                         ``(classifier.dimension, classifier.value)``.

    Convenience methods :meth:`for_target` and :meth:`with_value` wrap
    those indexes for caller ergonomics.
    """

    indexes = [
        IndexSpec(name="by_target",    key_fn=lambda c: c.target),
        IndexSpec(name="by_dimension", key_fn=lambda c: (c.dimension,)),
        IndexSpec(name="by_dim_value", key_fn=lambda c: (c.dimension, c.value)),
    ]

    def get_id(self, entity: Classifier) -> str:
        return entity.id

    # ---- convenience ----
    def for_target(self, target: EntityRef) -> Dict[str, Classifier]:
        """All classifiers on ``target``, keyed by ``dimension``.

        The plan §5.2 specifies a ``dict[str, Classifier]`` shape so
        callers can write::

            classifiers = graph.classifiers.for_target(file_ref)
            role = classifiers.get("role")     # Classifier | None

        If two classifiers share the same dimension on the same target
        (which violates the at-most-one-per-(target, dimension) invariant
        the plan calls out), the last one wins — same policy as the
        legacy ``compose_tags`` "classifier collisions prefer the last
        writer" (see ``src/enrichment/tagger/base.py``).
        """
        bucket = self.by_target[target]  # type: ignore[attr-defined]
        out: Dict[str, Classifier] = {}
        for cls_obj in bucket:
            out[cls_obj.dimension] = cls_obj
        return out

    def with_value(
        self, dimension: str, value: str
    ) -> Tuple[Classifier, ...]:
        """Every :class:`Classifier` matching ``(dimension, value)``."""
        return self.by_dim_value[(dimension, value)]  # type: ignore[attr-defined,no-any-return]

    def of_dimension(self, dimension: str) -> Tuple[Classifier, ...]:
        """Every :class:`Classifier` along ``dimension`` (any value).

        Mirrors :meth:`TraitRegistry.of_family`. The ``by_dimension`` index
        uses a 1-tuple per plan §5.2; the lookup unwraps that here.
        """
        return self.by_dimension[(dimension,)]  # type: ignore[attr-defined,no-any-return]


__all__ = ["TraitRegistry", "ClassifierRegistry"]
