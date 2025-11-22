from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Generic, TypeVar, List

T = TypeVar("T")

class GitParser(ABC, Generic[T]):
    @abstractmethod
    def parse(self, lines: List[str]) -> T:
        raise NotImplementedError
