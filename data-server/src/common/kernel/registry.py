"""Typed, indexed, picklable entity container.

Replaces the legacy ``common/registries.py:AbstractRegistry``. See §1.4.

Subclasses look like::

    class CommitRegistry(Registry[Commit, str]):
        indexes = [
            IndexSpec(name="by_author", key_fn=lambda c: c.author_ref),
            IndexSpec(name="by_file",   key_fn=lambda c: [ch.file_ref for ch in c.changes]),
        ]

        def get_id(self, entity: Commit) -> str:
            return entity.id

Indexes are declared as a ``ClassVar[list[IndexSpec]]`` and are exposed on
each instance under the name given in the spec (e.g. ``registry.by_author``).
They are rebuilt from entities on ``add`` / ``remove`` / ``reindex`` /
``load`` and are NOT pickled — so registry ``.pkl`` files stay small.
"""
from __future__ import annotations

import pickle
from abc import ABC, abstractmethod
from collections.abc import Collection, Iterator
from pathlib import Path
from typing import Any, ClassVar, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, PrivateAttr

from .entity import Entity
from .index import Index, IndexSpec

T = TypeVar("T", bound=Entity)
ID = TypeVar("ID")


def _registry_reconstruct(cls: type["Registry[Any, Any]"], items: list[Entity]) -> "Registry[Any, Any]":
    """Module-level helper for ``__reduce__`` — picklable by qualname."""
    inst = cls()
    for entity in items:
        inst.add(entity)  # type: ignore[arg-type]
    return inst


class Registry(BaseModel, Generic[T, ID], ABC):
    """Base class for every entity registry in the v2 graph.

    A registry owns one ``Entity`` subclass and tracks them by primary id.
    Secondary indexes declared in ``indexes`` are recomputed on mutations
    and on ``load()``.
    """

    # Pydantic config: registries hold mutable state and aren't validated
    # field-by-field on creation. We forbid extras for safety.
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    # ---- class-level metadata ----
    indexes: ClassVar[list[IndexSpec]] = []

    # ---- internal state (excluded from Pydantic field set) ----
    _items: dict[Any, T] = PrivateAttr(default_factory=dict)
    _indexes: dict[str, Index[Any, T]] = PrivateAttr(default_factory=dict)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def model_post_init(self, __context: Any) -> None:
        # Build empty Index instances for every declared spec.
        self._indexes = {spec.name: Index(spec) for spec in self.indexes}

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------
    @abstractmethod
    def get_id(self, entity: T) -> ID:
        """Return the primary id of an entity. Almost always ``entity.id``."""

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------
    def get(self, id: ID) -> Optional[T]:
        return self._items.get(id)

    def add(self, entity: T) -> T:
        """Insert (or replace) an entity. Returns the stored entity."""
        eid = self.get_id(entity)
        existing = self._items.get(eid)
        if existing is not None and existing is not entity:
            # Remove the old one from all indexes before replacing.
            for idx in self._indexes.values():
                idx._remove(existing)
        self._items[eid] = entity
        for idx in self._indexes.values():
            idx._add(entity)
        return entity

    def remove(self, id: ID) -> Optional[T]:
        entity = self._items.pop(id, None)
        if entity is None:
            return None
        for idx in self._indexes.values():
            idx._remove(entity)
        return entity

    def __iter__(self) -> Iterator[T]:  # type: ignore[override]
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, id: object) -> bool:
        return id in self._items

    def all(self) -> Collection[T]:
        """All entities. The returned view is a snapshot (tuple)."""
        return tuple(self._items.values())

    def ids(self) -> set[ID]:
        return set(self._items.keys())

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------
    def reindex(self) -> None:
        """Rebuild every declared index from the current entities."""
        # Reinstantiate so any stale buckets are dropped.
        self._indexes = {spec.name: Index(spec) for spec in self.indexes}
        for entity in self._items.values():
            for idx in self._indexes.values():
                idx._add(entity)

    def index(self, name: str) -> Index[Any, T]:
        """Look up a declared index by name (handy for tests / generic code)."""
        try:
            return self._indexes[name]
        except KeyError as e:
            raise AttributeError(
                f"{type(self).__name__} has no index {name!r} (declared: "
                f"{sorted(self._indexes)})"
            ) from e

    def __getattr__(self, item: str) -> Any:
        # Pydantic's ``BaseModel.__getattr__`` already handles private
        # attribute access (``_items``, ``_indexes``). We delegate to it
        # first, and if that raises, expose declared indexes by name (so
        # ``registry.by_author`` resolves to the Index instance).
        try:
            return super().__getattr__(item)  # type: ignore[misc]
        except AttributeError:
            pass
        # Use object.__getattribute__ to avoid recursion through this hook.
        try:
            private = object.__getattribute__(self, "__pydantic_private__")
        except AttributeError:
            raise AttributeError(item)
        if private is None:
            raise AttributeError(item)
        idx_map = private.get("_indexes")
        if idx_map and item in idx_map:
            return idx_map[item]
        raise AttributeError(item)

    # ------------------------------------------------------------------
    # Pickle hooks — indexes are excluded.
    # ------------------------------------------------------------------
    def __reduce__(self) -> tuple[Any, ...]:
        items = list(self._items.values())
        return (_registry_reconstruct, (type(self), items))

    def dump(self, path: Path) -> None:
        """Pickle this registry to ``path``. Indexes are not written."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump(self, fh, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def load(cls, path: Path) -> "Registry[T, ID]":
        """Unpickle a registry from ``path`` and rebuild its indexes."""
        with Path(path).open("rb") as fh:
            obj = pickle.load(fh)
        if not isinstance(obj, cls):
            raise TypeError(
                f"Pickle at {path} contained {type(obj).__name__}, expected "
                f"{cls.__name__}"
            )
        # ``_registry_reconstruct`` already rebuilt the indexes via ``add``,
        # but call ``reindex`` once more to guarantee a clean state.
        obj.reindex()
        return obj


__all__ = ["Registry"]
