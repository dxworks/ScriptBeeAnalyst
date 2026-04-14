from __future__ import annotations

from typing import Any, Dict, List

from src.common.unified_author import SourceIdentity
from src.logger import get_logger

LOG = get_logger(__name__)


def extract_git_identities(git_project) -> List[SourceIdentity]:
    """Extract SourceIdentity instances from all GitAccounts in a GitProject."""
    identities = []
    for account in git_project.account_registry.all:
        identities.append(SourceIdentity(
            source="git",
            name=account.git_id.name,
            email=account.git_id.email,
            login=None,
            source_key=str(account.git_id),  # "name <email>"
        ))
    return identities


def extract_github_identities(github_project) -> List[SourceIdentity]:
    """Extract SourceIdentity instances from all GitHubUsers in a GitHubProject."""
    identities = []
    for user in github_project.git_hub_user_registry.all:
        identities.append(SourceIdentity(
            source="github",
            name=user.name or user.login or "unknown",
            email=None,  # GitHub users don't have emails in our model
            login=user.login,
            source_key=user.url,
        ))
    return identities


def extract_jira_identities(jira_project) -> List[SourceIdentity]:
    """Extract SourceIdentity instances from all JiraUsers in a JiraProject."""
    identities = []
    for user in jira_project.jira_user_registry.all:
        identities.append(SourceIdentity(
            source="jira",
            name=user.name,
            email=None,  # JIRA users don't have emails in our model
            login=user.key,
            source_key=user.link,
        ))
    return identities


def extract_all_identities(graph_data: Dict[str, Any]) -> List[SourceIdentity]:
    """
    Extract all SourceIdentity instances from a loaded graph_data dict.
    Safely handles missing sources (e.g., project with only Git data).
    """
    identities: List[SourceIdentity] = []

    git_project = graph_data.get("git")
    if git_project is not None:
        git_ids = extract_git_identities(git_project)
        LOG.info(f"Extracted {len(git_ids)} git identities")
        identities.extend(git_ids)

    github_project = graph_data.get("github")
    if github_project is not None:
        github_ids = extract_github_identities(github_project)
        LOG.info(f"Extracted {len(github_ids)} github identities")
        identities.extend(github_ids)

    jira_project = graph_data.get("jira")
    if jira_project is not None:
        jira_ids = extract_jira_identities(jira_project)
        LOG.info(f"Extracted {len(jira_ids)} jira identities")
        identities.extend(jira_ids)

    LOG.info(f"Total identities extracted: {len(identities)}")
    return identities
