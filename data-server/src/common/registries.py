from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, Dict, Collection, Set, Optional, TypeVar

TYPE = TypeVar("TYPE")
ID = TypeVar("ID")

class AbstractRegistry(Generic[TYPE, ID], ABC):
    def __init__(self) -> None:
        self._map: Dict[ID, TYPE] = {}

    @property
    def all(self) -> Collection[TYPE]:
        return self._map.values()

    @property
    def all_ids(self) -> Set[ID]:
        return set(self._map.keys())

    def get_by_id(self, id: ID) -> Optional[TYPE]:
        return self._map.get(id)

    def contains(self, id: ID) -> bool:
        return id in self._map

    def add(self, entity: TYPE, id: Optional[ID] = None) -> Optional[TYPE]:
        if id is None:
            id = self.get_id(entity)
        return self._map.setdefault(id, entity)

    def add_all(self, entities: Collection[TYPE]) -> None:
        for entity in entities:
            self._map[self.get_id(entity)] = entity

    def remove(self, id: ID) -> Optional[TYPE]:
        return self._map.pop(id, None)

    def delete(self, entity: TYPE) -> Optional[TYPE]:
        return self._map.pop(self.get_id(entity), None)

    def is_empty(self) -> bool:
        return len(self._map) == 0

    @abstractmethod
    def get_id(self, entity: TYPE) -> ID:
        ...
