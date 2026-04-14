from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import uuid4


@dataclass(frozen=True)
class SourceIdentity:
    """
    A single identity from one data source, normalized for cross-source comparison.
    This is the adapter between source-specific models (GitAccount, GitHubUser, JiraUser)
    and the smart merge engine.
    """
    source: str          # "git", "github", "jira"
    name: str            # display name (always present)
    email: Optional[str] # email (git always has, github/jira may not)
    login: Optional[str] # github login or jira key
    source_key: str      # unique key within source registry

    @property
    def key(self) -> str:
        """Globally unique key: source + source_key."""
        return f"{self.source}:{self.source_key}"

    @property
    def display_label(self) -> str:
        """Human-readable label for UI display."""
        parts = [self.name]
        if self.email:
            parts.append(f"<{self.email}>")
        if self.login:
            parts.append(f"@{self.login}")
        return " ".join(parts)


class UnifiedUser:
    """
    A merged identity aggregating accounts across sources.
    Created when a user accepts an author-matching suggestion.
    """
    def __init__(
        self,
        display_name: str,
        primary_email: Optional[str] = None,
        identities: Optional[List[SourceIdentity]] = None,
        id: Optional[str] = None,
    ):
        self.id = id or str(uuid4())
        self.display_name = display_name
        self.primary_email = primary_email
        self.identities: List[SourceIdentity] = identities or []
        self._graph_data: Optional[Dict[str, Any]] = None

    def bind_graph(self, graph_data: Dict[str, Any]) -> None:
        """Bind to a loaded graph so convenience accessors can resolve live references."""
        self._graph_data = graph_data

    @property
    def git_identities(self) -> List[SourceIdentity]:
        return [i for i in self.identities if i.source == "git"]

    @property
    def github_identities(self) -> List[SourceIdentity]:
        return [i for i in self.identities if i.source == "github"]

    @property
    def jira_identities(self) -> List[SourceIdentity]:
        return [i for i in self.identities if i.source == "jira"]

    @property
    def git_accounts(self) -> list:
        """Resolve live GitAccount references from the loaded graph."""
        if not self._graph_data:
            return []
        git_project = self._graph_data.get("git")
        if not git_project:
            return []
        registry = git_project.account_registry
        accounts = []
        for identity in self.git_identities:
            account = registry.get_by_id(identity.source_key)
            if account is not None:
                accounts.append(account)
        return accounts

    @property
    def github_users(self) -> list:
        """Resolve live GitHubUser references from the loaded graph."""
        if not self._graph_data:
            return []
        github_project = self._graph_data.get("github")
        if not github_project:
            return []
        registry = github_project.git_hub_user_registry
        users = []
        for identity in self.github_identities:
            user = registry.get_by_id(identity.source_key)
            if user is not None:
                users.append(user)
        return users

    @property
    def jira_users(self) -> list:
        """Resolve live JiraUser references from the loaded graph."""
        if not self._graph_data:
            return []
        jira_project = self._graph_data.get("jira")
        if not jira_project:
            return []
        registry = jira_project.jira_user_registry
        users = []
        for identity in self.jira_identities:
            user = registry.get_by_id(identity.source_key)
            if user is not None:
                users.append(user)
        return users

    @property
    def all_emails(self) -> List[str]:
        return list({i.email for i in self.identities if i.email})

    @property
    def all_names(self) -> List[str]:
        return list({i.name for i in self.identities})

    @property
    def all_logins(self) -> List[str]:
        return list({i.login for i in self.identities if i.login})

    @property
    def commit_count(self) -> int:
        return sum(len(ga.commits) for ga in self.git_accounts)

    @property
    def issue_count(self) -> int:
        count = 0
        for ju in self.jira_users:
            seen = set()
            for issue_list in (ju.issues_as_reporter, ju.issues_as_creator, ju.issues_as_assignee):
                for issue in issue_list:
                    if issue.key not in seen:
                        seen.add(issue.key)
                        count += 1
        return count

    @property
    def pr_count(self) -> int:
        count = 0
        for gu in self.github_users:
            seen = set()
            for pr_list in (gu.pull_requests_as_creator, gu.pull_requests_as_merged_by, gu.pull_requests_as_assignee):
                for pr in pr_list:
                    if pr.number not in seen:
                        seen.add(pr.number)
                        count += 1
        return count

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict for API responses."""
        return {
            "id": self.id,
            "display_name": self.display_name,
            "primary_email": self.primary_email,
            "identities": [
                {
                    "source": i.source,
                    "source_key": i.source_key,
                    "name": i.name,
                    "email": i.email,
                    "login": i.login,
                }
                for i in self.identities
            ],
            "stats": {
                "commit_count": self.commit_count,
                "issue_count": self.issue_count,
                "pr_count": self.pr_count,
            },
        }

    def __repr__(self) -> str:
        return f"UnifiedUser(id={self.id!r}, name={self.display_name!r}, identities={len(self.identities)})"
