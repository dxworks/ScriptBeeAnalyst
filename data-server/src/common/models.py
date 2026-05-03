"""Domain models re-export hub.

All domain classes are defined in per-domain modules (base_models, git_models,
jira_models, github_models) and re-exported here so that existing imports like
    from src.common.models import GitProject, Issue, PullRequest
continue to work unchanged.

Pickle compatibility is maintained by setting __module__ = 'src.common.models'
on all serialized classes (done in git_models.py).
"""

# Re-export all domain classes
from src.common.base_models import Project, Account, Developer, AccountType  # noqa: F401
from src.common.git_models import (  # noqa: F401
    LineOperation,
    ChangeType,
    GitAccountId,
    GitAccount,
    LineChange,
    Hunk,
    File,
    GitCommit,
    Change,
    GitProject,
)
from src.common.jira_models import (  # noqa: F401
    IssueStatusCategory,
    IssueStatus,
    IssueType,
    Issue,
    JiraUser,
    JiraProject,
    Comment as JiraComment,
    Change as JiraChange,
    ChangeItem as JiraChangeItem,
)
from src.common.github_models import (  # noqa: F401
    GitHubUser,
    PullRequest,
    GitHubCommit,
    GitHubProject,
    Review,
    ReviewComment,
)

# ── Forward Reference Resolution ────────────────────────────────────────────────
# Cross-domain forward references (e.g. GitCommit.issues -> Issue) can only be
# resolved once all classes are available in the same namespace.
# Include typing constructs since from __future__ import annotations makes all
# annotations into strings that need resolution.
import typing as _typing
import datetime as _datetime
import uuid as _uuid

_ns = {k: v for k, v in globals().items()}
_ns.update({k: v for k, v in vars(_typing).items() if not k.startswith("_")})
_ns.update({"datetime": _datetime.datetime, "timedelta": _datetime.timedelta, "uuid": _uuid})

# Git models referencing JIRA/GitHub types
LineChange.model_rebuild(_types_namespace=_ns)
Hunk.model_rebuild(_types_namespace=_ns)
GitAccountId.model_rebuild(_types_namespace=_ns)
GitAccount.model_rebuild(_types_namespace=_ns)
File.model_rebuild(_types_namespace=_ns)
GitCommit.model_rebuild(_types_namespace=_ns)
Change.model_rebuild(_types_namespace=_ns)
GitProject.model_rebuild(_types_namespace=_ns)

# JIRA models referencing Git/GitHub types
IssueStatusCategory.model_rebuild(_types_namespace=_ns)
IssueStatus.model_rebuild(_types_namespace=_ns)
IssueType.model_rebuild(_types_namespace=_ns)
JiraChangeItem.model_rebuild(_types_namespace=_ns)
JiraChange.model_rebuild(_types_namespace=_ns)
JiraComment.model_rebuild(_types_namespace=_ns)
Issue.model_rebuild(_types_namespace=_ns)
JiraUser.model_rebuild(_types_namespace=_ns)

# GitHub models referencing Git/JIRA types
GitHubUser.model_rebuild(_types_namespace=_ns)
ReviewComment.model_rebuild(_types_namespace=_ns)
Review.model_rebuild(_types_namespace=_ns)
PullRequest.model_rebuild(_types_namespace=_ns)
GitHubCommit.model_rebuild(_types_namespace=_ns)
GitHubProject.model_rebuild(_types_namespace=_ns)
