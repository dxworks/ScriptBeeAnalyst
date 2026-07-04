"""Typed cross-registry pointer.

Every cross-entity reference in the v2 graph is an ``EntityRef`` — never a
Python object reference. This kills the cycle problem (P7) at the type level
and makes pickling trivially per-registry. See §1.2 of the architectural plan.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, ConfigDict

from .kinds import EntityKind

if TYPE_CHECKING:  # avoid runtime cycles; forward declarations only
    from .entity import Entity
    from .graph import Graph


class EntityRef(BaseModel):
    """Frozen, hashable pointer to an entity by ``(kind, id)``.

    Resolving a ref is an O(1) dict lookup through the registry that owns
    ``kind`` — see ``Graph.registry_for``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EntityKind
    id: str

    def resolve(self, graph: "Graph") -> Optional["Entity"]:
        """Look the entity up in ``graph``. Returns ``None`` if missing."""
        registry = graph.registry_for(self.kind)
        if registry is None:
            return None
        return registry.get(self.id)


__all__ = ["EntityRef"]
