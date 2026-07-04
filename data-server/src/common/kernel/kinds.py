"""Closed set of entity kinds in the v2 graph.

Every node in the graph carries a ``kind: EntityKind`` ClassVar and every
cross-entity reference is an ``EntityRef(kind, id)``. Adding a new data
source = adding a member here. See ``architectural_changes.md`` §1.1.
"""
from __future__ import annotations

from enum import StrEnum


class EntityKind(StrEnum):
    # --- projects ---
    PROJECT = "project"

    # --- people ---
    GIT_ACCOUNT = "git_account"
    JIRA_USER = "jira_user"
    GITHUB_USER = "github_user"
    UNIFIED_USER = "unified_user"

    # --- git domain ---
    COMMIT = "commit"
    FILE = "file"
    CHANGE = "change"
    HUNK = "hunk"

    # --- jira domain ---
    ISSUE = "issue"
    ISSUE_STATUS = "issue_status"
    ISSUE_TYPE = "issue_type"

    # --- github domain ---
    PULL_REQUEST = "pull_request"
    REVIEW = "review"
    REVIEW_COMMENT = "review_comment"
    GITHUB_COMMIT = "github_commit"

    # --- code structure / lizard / quality / duplication ---
    CODE_TYPE = "code_type"
    CODE_METHOD = "code_method"
    CODE_FIELD = "code_field"
    CODE_REF = "code_ref"
    FILE_METRIC = "file_metric"
    DUPLICATION_PAIR = "duplication_pair"
    QUALITY_ISSUE = "quality_issue"

    # --- enrichment ---
    COMPONENT = "component"
    TRAIT = "trait"
    CLASSIFIER = "classifier"
    RELATION = "relation"

    # --- future ---
    APP_TAG = "app_tag"  # task 7 (app inspector)


__all__ = ["EntityKind"]
