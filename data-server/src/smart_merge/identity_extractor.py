"""Derive :class:`SourceIdentity` instances from the typed v2 :class:`Graph`.

Per Phase-2 decision D5: smart-merge is a self-contained engine whose
input is a :class:`~src.common.kernel.Graph` and whose output is a set of
:class:`SourceIdentity` clusters → :class:`UnifiedUser` records (persisted
into Supabase ``unified_users`` / ``user_identity_mappings``).

This module replaces the legacy ``src.common.author_extractor`` which
read off the deleted ``graph_data: dict`` global. The shapes emitted are
identical (same ``source`` / ``name`` / ``email`` / ``login`` /
``source_key``) so the persisted ``rejected_similarities`` rows from
before the migration replay verbatim — see Chunk 19 handoff §SourceIdentity.

Key invariant for replay compatibility: ``source_key`` MUST match what
v1 wrote into ``user_identity_mappings.source_key``:

* git    — ``GitAccount.id`` == ``"name <email>"`` (the legacy
            ``str(GitAccountId)``).
* github — ``GitHubUser.id`` == the user's URL.
* jira   — ``JiraUser.id``  == the user's link URL.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from src.logger import get_logger
from src.smart_merge.identity import SourceIdentity

if TYPE_CHECKING:  # forward-only — keep this module domain-free at import time
    from src.common.kernel import Graph

LOG = get_logger(__name__)


def extract_git_identities(graph: "Graph") -> List[SourceIdentity]:
    """Build a :class:`SourceIdentity` for every :class:`GitAccount`."""
    identities: List[SourceIdentity] = []
    for account in graph.git_accounts.all():
        identities.append(SourceIdentity(
            source="git",
            name=account.name,
            email=account.email,
            login=None,
            source_key=account.id,  # "name <email>" — matches legacy str(GitAccountId)
        ))
    return identities


def extract_github_identities(graph: "Graph") -> List[SourceIdentity]:
    """Build a :class:`SourceIdentity` for every :class:`GitHubUser`."""
    identities: List[SourceIdentity] = []
    for user in graph.github_users.all():
        identities.append(SourceIdentity(
            source="github",
            name=user.name or user.login or "unknown",
            email=None,             # v2 GitHubUser carries no email
            login=user.login,
            source_key=user.id,     # == user.url (Chunk 6 invariant)
        ))
    return identities


def extract_jira_identities(graph: "Graph") -> List[SourceIdentity]:
    """Build a :class:`SourceIdentity` for every :class:`JiraUser`."""
    identities: List[SourceIdentity] = []
    for user in graph.jira_users.all():
        identities.append(SourceIdentity(
            source="jira",
            name=user.name,
            email=None,             # v2 JiraUser carries no email
            login=user.key,         # Jira's per-project key (legacy login slot)
            source_key=user.id,     # == user.link (Chunk 5 invariant)
        ))
    return identities


def extract_all_identities(graph: "Graph") -> List[SourceIdentity]:
    """Extract every per-source identity from a typed v2 :class:`Graph`.

    Safely handles graphs whose per-source registries are empty (e.g. a
    project with only Git data) — the matching per-source helper
    contributes zero identities in that case.
    """
    identities: List[SourceIdentity] = []

    git_ids = extract_git_identities(graph)
    if git_ids:
        LOG.info(f"Extracted {len(git_ids)} git identities")
    identities.extend(git_ids)

    github_ids = extract_github_identities(graph)
    if github_ids:
        LOG.info(f"Extracted {len(github_ids)} github identities")
    identities.extend(github_ids)

    jira_ids = extract_jira_identities(graph)
    if jira_ids:
        LOG.info(f"Extracted {len(jira_ids)} jira identities")
    identities.extend(jira_ids)

    LOG.info(f"Total identities extracted: {len(identities)}")
    return identities


__all__ = [
    "extract_all_identities",
    "extract_git_identities",
    "extract_github_identities",
    "extract_jira_identities",
]
