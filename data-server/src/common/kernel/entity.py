"""Universal entity base.

Every node in the v2 graph is an ``Entity`` subclass. Each subclass declares
its ``kind`` as a ``ClassVar[EntityKind]``. See §1.3 of the plan.
"""
from __future__ import annotations

from abc import ABC
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from .kinds import EntityKind
from .ref import EntityRef


def _entity_reconstruct(cls: type["Entity"], state: dict[str, Any]) -> "Entity":
    """Module-level helper so pickle can find it by qualname."""
    return cls.from_state(state)


class Entity(BaseModel, ABC):
    """Common base for every graph node.

    Concrete subclasses MUST override ``kind`` with the matching
    ``EntityKind`` member. ``extra="forbid"`` enforces the "no untyped
    attributes" rule at validation time.
    """

    # Subclasses override this ClassVar. The base intentionally has no
    # default — concrete subclasses are validated by ``__init_subclass__``.
    kind: ClassVar[EntityKind]

    model_config = ConfigDict(frozen=False, extra="forbid")

    id: str

    def __init_subclass__(cls, abstract: bool = False, **kwargs: Any) -> None:
        """Validate that every concrete subclass declares ``kind``.

        Intermediate abstract bases (e.g. ``Account``, ``Tag``) opt out by
        passing ``abstract=True`` in the class statement::

            class Account(Entity, abstract=True):
                name: str
                ...

        Concrete leaves MUST declare ``kind`` either on the class itself or
        on a (non-Entity) ancestor in the MRO.
        """
        super().__init_subclass__(**kwargs)
        # Pydantic re-runs __init_subclass__ on its synthesized subclass
        # machinery; treat ABC-flagged classes (with unresolved
        # __abstractmethods__) as abstract too.
        if abstract or getattr(cls, "__abstractmethods__", None):
            # Stash the flag on the class for downstream introspection
            # (e.g. ``LazyRegistryProxy`` does not need it but tooling may).
            cls.__entity_abstract__ = True
            return
        cls.__entity_abstract__ = False
        if "kind" not in cls.__dict__:
            declared = False
            for base in cls.__mro__[1:]:
                if base is Entity:
                    break
                if "kind" in base.__dict__:
                    declared = True
                    break
            if not declared:
                raise TypeError(
                    f"Concrete Entity subclass {cls.__name__!r} must declare "
                    f"``kind: ClassVar[EntityKind] = EntityKind.<X>``. Pass "
                    f"``abstract=True`` in the class statement to opt out "
                    f"(intermediate bases like Account/Tag)."
                )

    def ref(self) -> EntityRef:
        """Return a typed pointer to this entity."""
        return EntityRef(kind=type(self).kind, id=self.id)

    # ------------------------------------------------------------------
    # Pickle hooks. The default behavior delegates to Pydantic
    # ``model_dump`` / ``model_validate`` so every Entity is picklable
    # without each subclass writing its own ``__reduce__``. Subclasses can
    # override either method to customize serialization. See §6/§8.
    # ------------------------------------------------------------------
    def __reduce__(self) -> tuple[Any, ...]:
        state = self.model_dump(mode="python")
        return (_entity_reconstruct, (type(self), state))

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "Entity":
        """Reconstruct an instance from a ``model_dump`` payload."""
        return cls.model_validate(state)


__all__ = ["Entity"]
