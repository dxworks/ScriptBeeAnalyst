from __future__ import annotations
from typing import TYPE_CHECKING
from src.common.registries import AbstractRegistry

if TYPE_CHECKING:
    from src.common.models import GitHubUser, PullRequest, GitHubCommit


class GitHubUserRegistry(AbstractRegistry["GitHubUser", str]):
    def get_id(self, entity: "GitHubUser") -> str:
        return entity.url

class PullRequestRegistry(AbstractRegistry["PullRequest", int]):
    def get_id(self, entity: "PullRequest") -> int:
        return entity.number

class GitHubCommitRegistry(AbstractRegistry["GitHubCommit", str]):
    def get_id(self, entity: "GitHubCommit") -> str:
        return entity.id

