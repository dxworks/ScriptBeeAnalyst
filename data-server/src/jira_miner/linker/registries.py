from __future__ import annotations
from typing import TYPE_CHECKING
from src.common.registries import AbstractRegistry

if TYPE_CHECKING:
    # Only imported for type hints, not at runtime
    from src.common.models import IssueStatusCategory, IssueStatus, IssueType, Issue, JiraUser


class IssueStatusCategoryRegistry(AbstractRegistry["IssueStatusCategory", str]):
    def get_id(self, entity: "IssueStatusCategory") -> str:
        return entity.key


class IssueStatusRegistry(AbstractRegistry["IssueStatus", str]):
    def get_id(self, entity: "IssueStatus") -> str:
        return entity.id


class IssueTypeRegistry(AbstractRegistry["IssueType", str]):
    def get_id(self, entity: "IssueType") -> str:
        return entity.name


class IssueRegistry(AbstractRegistry["Issue", str]):
    def get_id(self, entity: "Issue") -> str:
        return entity.key


class JiraUserRegistry(AbstractRegistry["JiraUser", str]):
    def get_id(self, entity: "JiraUser") -> str:
        return entity.link
