"""
Abstract persistence interface for the smart merge engine.
Implementations provide storage for rejected similarities and user mappings.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, List

from src.smart_merge.types import RejectedPair, UserMapping


class SmartMergeRepository(ABC):
    @abstractmethod
    def get_rejected_similarities(self, project_id: str) -> List[RejectedPair]:
        raise NotImplementedError

    @abstractmethod
    def add_rejected_similarities(
        self,
        project_id: str,
        pairs: Iterable[RejectedPair],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_user_mappings(self, project_id: str) -> List[UserMapping]:
        raise NotImplementedError

    @abstractmethod
    def upsert_user_mapping(self, mapping: UserMapping, project_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_user_mapping(self, project_id: str, unified_user_id: str) -> None:
        raise NotImplementedError
