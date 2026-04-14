from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field

from src.common.base_models import Project
from src.common.identity import identity_fields
from src.jira_miner.linker.registries import (
    IssueStatusCategoryRegistry,
    IssueStatusRegistry,
    IssueTypeRegistry,
    IssueRegistry,
    JiraUserRegistry,
)


# ── Entities ────────────────────────────────────────────────────────────────────

@identity_fields("key", "name")
class IssueStatusCategory(BaseModel):
    key: str
    name: str

    issue_statuses: List[IssueStatus] = Field(default_factory=list)


@identity_fields("id", "name")
class IssueStatus(BaseModel):
    id: str
    name: str

    issue_status_categories: IssueStatusCategory = Field(default_factory=IssueStatusCategory)
    issues: List[Issue] = Field(default_factory=list)


@identity_fields("id", "name", "description", "isSubTask")
class IssueType(BaseModel):
    id: str
    name: str
    description: str
    isSubTask: bool

    issues: List[Issue] = Field(default_factory=list)


@identity_fields("id", "key", "summary", "createdAt", "updatedAt")
class Issue(BaseModel):
    id: int
    key: str
    summary: str
    createdAt: datetime
    updatedAt: datetime

    issue_statuses: List[IssueStatus] = Field(default_factory=list)
    issue_types: List[IssueType] = Field(default_factory=list)
    creator: Optional[JiraUser] = None
    jira_users_as_assignee: List[JiraUser] = Field(default_factory=list)
    reporter: Optional[JiraUser] = None
    parent: Optional[Issue] = None
    children: List[Issue] = Field(default_factory=list)

    git_commits: List[GitCommit] = Field(default_factory=list)
    pull_requests: List[PullRequest] = Field(default_factory=list)


@identity_fields("key", "name", "link")
class JiraUser(BaseModel):
    key: str
    name: str
    link: str

    issues_as_reporter: List[Issue] = Field(default_factory=list)
    issues_as_creator: List[Issue] = Field(default_factory=list)
    issues_as_assignee: List[Issue] = Field(default_factory=list)


# ── Project ─────────────────────────────────────────────────────────────────────

class JiraProject(Project):
    name: str
    issue_status_category_registry: IssueStatusCategoryRegistry = Field(default_factory=IssueStatusCategoryRegistry)
    issue_status_registry: IssueStatusRegistry = Field(default_factory=IssueStatusRegistry)
    issue_type_registry: IssueTypeRegistry = Field(default_factory=IssueTypeRegistry)
    issue_registry: IssueRegistry = Field(default_factory=IssueRegistry)
    jira_user_registry: JiraUserRegistry = Field(default_factory=JiraUserRegistry)

    def __str__(self):
        return (
            f"JiraProject(name={self.name},\n"
            f"issue_status_category_registry: {len(self.issue_status_category_registry.all)},\n"
            f"issue_status_registry: {len(self.issue_status_registry.all)},\n"
            f"issue_type_registry: {len(self.issue_type_registry.all)},\n"
            f"issue_registry: {len(self.issue_registry.all)},\n"
            f"jira_user_registry: {len(self.jira_user_registry.all)})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, JiraProject):
            return False
        return (
            self.name == other.name
            and self.issue_status_category_registry._map == other.issue_status_category_registry._map
            and self.issue_status_registry._map == other.issue_status_registry._map
            and self.issue_type_registry._map == other.issue_type_registry._map
            and self.issue_registry._map == other.issue_registry._map
            and self.jira_user_registry._map == other.jira_user_registry._map
        )
