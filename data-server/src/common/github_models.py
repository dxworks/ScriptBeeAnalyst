from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field

from src.common.base_models import Project
from src.common.identity import identity_fields
from src.github_miner.linker.registries import (
    GitHubUserRegistry,
    PullRequestRegistry,
    GitHubCommitRegistry,
)


# ── Entities ────────────────────────────────────────────────────────────────────

@identity_fields("url", "login", "name")
class GitHubUser(BaseModel):
    url: str
    login: Optional[str]
    name: Optional[str]

    pull_requests_as_creator: List[PullRequest] = Field(default_factory=list)
    pull_requests_as_merged_by: List[PullRequest] = Field(default_factory=list)
    pull_requests_as_assignee: List[PullRequest] = Field(default_factory=list)


@identity_fields("number", "title", "state", "changedFiles", "body",
                  "createdAt", "mergedAt", "closedAt", "updatedAt")
class PullRequest(BaseModel):
    number: int
    title: str
    state: str
    changedFiles: int
    body: str
    createdAt: datetime
    mergedAt: Optional[datetime]
    closedAt: Optional[datetime]
    updatedAt: Optional[datetime]

    createdBy: Optional[GitHubUser] = None
    assignees: List[GitHubUser] = Field(default_factory=list)
    mergedBy: Optional[GitHubUser] = None
    git_hub_commits: List[GitHubCommit] = Field(default_factory=list)

    issues: List[Issue] = Field(default_factory=list)
    git_commits: List[GitCommit] = Field(default_factory=list)


@identity_fields("id", "date", "message", "changedFiles")
class GitHubCommit(BaseModel):
    id: str
    date: datetime
    message: str
    changedFiles: int

    pull_requests: List[PullRequest] = Field(default_factory=list)


# ── Project ─────────────────────────────────────────────────────────────────────

class GitHubProject(Project):
    name: str
    git_hub_user_registry: GitHubUserRegistry = Field(default_factory=GitHubUserRegistry)
    pull_request_registry: PullRequestRegistry = Field(default_factory=PullRequestRegistry)
    git_hub_commit_registry: GitHubCommitRegistry = Field(default_factory=GitHubCommitRegistry)

    def __str__(self):
        return (
            f"GitHubProject(name={self.name},\n"
            f"git_hub_user_registry: {len(self.git_hub_user_registry.all)},\n"
            f"pull_request_registry: {len(self.pull_request_registry.all)},\n"
            f"git_hub_commit_registry: {len(self.git_hub_commit_registry.all)}\n"
            ")"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GitHubProject):
            return False
        return (
            self.name == other.name
            and self.git_hub_user_registry._map == other.git_hub_user_registry._map
            and self.pull_request_registry._map == other.pull_request_registry._map
            and self.git_hub_commit_registry._map == other.git_hub_commit_registry._map
        )
