"""Declarative secondary indexes for registries.

A registry declares ``indexes: ClassVar[list[IndexSpec]] = [...]``; each spec
becomes an attribute on the registry instance after ``reindex()``. Indexes
are NEVER pickled — they are recomputed from entities on load. See §1.5.
"""
from __future__ import annotations

from collections.abc import Iterable, KeysView
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

K = TypeVar("K")
V = TypeVar("V")


class IndexSpec(BaseModel):
    """Declaration of a single secondary index on a registry.

    ``key_fn`` may return either a single key OR an iterable of keys. When
    it returns an iterable, the entity is added under every yielded key
    (multi-key index — e.g. "by_file" on a commit with several changes).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str
    key_fn: Callable[[Any], Any]
    # When ``multi`` is True, several entities can share a key (the index
    # value is a set). When False, the index is a 1:1 mapping (the value is
    # a single entity); duplicate keys raise on ``reindex``.
    multi: bool = True


def _normalize_keys(raw: Any) -> list[Any]:
    """Turn the output of a ``key_fn`` into a list of keys.

    A ``key_fn`` may return:
      * ``None`` — entity is not indexed (skipped).
      * a single hashable key — wrapped in a 1-element list.
      * an iterable of keys — flattened.

    Tuples and Pydantic models (e.g. ``EntityRef``) are treated as single
    keys because they are themselves hashable composite values.
    """
    if raw is None:
        return []
    # Pydantic models (EntityRef is the common case) are hashable composites.
    if isinstance(raw, BaseModel):
        return [raw]
    # str/bytes are iterable but should be single keys.
    if isinstance(raw, (str, bytes)):
        return [raw]
    # Tuples are composite hashable keys, not iterables-of-keys.
    if isinstance(raw, tuple):
        return [raw]
    if isinstance(raw, Iterable):
        return list(raw)
    return [raw]


class Index(Generic[K, V]):
    """Concrete in-memory index built by ``Registry.reindex``.

    Read-only from outside: ``registry.<index_name>[key]`` returns the
    matching collection. For ``multi=True`` indexes, the collection is a
    tuple of entities (stable ordering); missing keys return an empty
    tuple. For ``multi=False`` indexes, the result is the single entity
    or ``None``.

    Entities are tracked inside multi-buckets by their ``.id`` attribute
    (unique within a registry) so we never depend on Pydantic-model
    hashability.
    """

    __slots__ = ("_spec", "_multi", "_data")

    def __init__(self, spec: IndexSpec) -> None:
        self._spec = spec
        self._multi = spec.multi
        # multi=True: dict[key, dict[entity_id, entity]]
        # multi=False: dict[key, entity]
        self._data: dict[K, Any] = {}

    # ----- introspection -----
    @property
    def name(self) -> str:
        return self._spec.name

    @property
    def spec(self) -> IndexSpec:
        return self._spec

    # ----- read API -----
    def __getitem__(self, key: K) -> Any:
        if self._multi:
            bucket = self._data.get(key)
            if bucket is None:
                return tuple()
            return tuple(bucket.values())
        return self._data.get(key)

    def get(self, key: K, default: Any = None) -> Any:
        if self._multi:
            bucket = self._data.get(key)
            if bucket is None:
                return tuple() if default is None else default
            return tuple(bucket.values())
        return self._data.get(key, default)

    def keys(self) -> KeysView[K]:
        return self._data.keys()

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    # ----- mutation (used by Registry.reindex / add / remove) -----
    def _clear(self) -> None:
        self._data.clear()

    def _add(self, entity: V) -> None:
        entity_id = getattr(entity, "id")
        for key in _normalize_keys(self._spec.key_fn(entity)):
            if self._multi:
                bucket = self._data.get(key)
                if bucket is None:
                    bucket = {}
                    self._data[key] = bucket
                bucket[entity_id] = entity
            else:
                existing = self._data.get(key)
                if existing is not None and getattr(existing, "id") != entity_id:
                    raise ValueError(
                        f"Index {self._spec.name!r} (multi=False) already has "
                        f"a value for key {key!r}"
                    )
                self._data[key] = entity

    def _remove(self, entity: V) -> None:
        entity_id = getattr(entity, "id")
        for key in _normalize_keys(self._spec.key_fn(entity)):
            if self._multi:
                bucket = self._data.get(key)
                if bucket is None:
                    continue
                bucket.pop(entity_id, None)
                if not bucket:
                    self._data.pop(key, None)
            else:
                current = self._data.get(key)
                if current is not None and getattr(current, "id") == entity_id:
                    self._data.pop(key, None)


__all__ = ["IndexSpec", "Index"]
