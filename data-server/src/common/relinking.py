from __future__ import annotations

from typing import TYPE_CHECKING

from src.logger import get_logger

if TYPE_CHECKING:
    from src.common.git_models import GitProject

LOG = get_logger(__name__)


def _resolve_single(registry, temp_id, label):
    """Look up an entity by ID, warn if missing."""
    entity = registry.get_by_id(temp_id)
    if entity is None:
        LOG.warning(f"Could not find {label} {temp_id} in registry")
    return entity


def _resolve_and_append(registry, temp_ids, target_list, label):
    """Resolve a list of IDs and append results to target list."""
    for temp_id in temp_ids:
        target_list.append(_resolve_single(registry, temp_id, label))


def relink_git_objects(project: GitProject):
    """Restore cross-object references after deserialization.

    During pickle, objects store related entity IDs in temporary _fields.
    This function resolves those IDs back to live object references and
    cleans up the temporary attributes.
    """
    for account in project.account_registry.all:
        _resolve_and_append(
            project.git_commit_registry, account._commits,
            account.commits, "commit",
        )
        del account._commits
        account.project = project

    for commit in project.git_commit_registry.all:
        commit.author = _resolve_single(
            project.account_registry, str(commit._author), "author",
        )
        commit.committer = _resolve_single(
            project.account_registry, str(commit._committer), "committer",
        )
        _resolve_and_append(
            project.git_commit_registry, commit._parents,
            commit.parents, "parent commit",
        )
        _resolve_and_append(
            project.git_commit_registry, commit._children,
            commit.children, "child commit",
        )
        _resolve_and_append(
            project.change_registry, commit._changes,
            commit.changes, "change",
        )
        del commit._author, commit._committer
        del commit._parents, commit._children, commit._changes
        commit.project = project

    for file in project.file_registry.all:
        _resolve_and_append(
            project.change_registry, file._changes,
            file.changes, "change",
        )
        del file._changes
        file.project = project

    for change in project.change_registry.all:
        change.commit = _resolve_single(
            project.git_commit_registry, change._commit, "commit",
        )
        change.file = _resolve_single(
            project.file_registry, change._file, "file",
        )
        if change._parent_commit is not None:
            change.parent_commit = _resolve_single(
                project.git_commit_registry, change._parent_commit, "parent commit",
            )
        _resolve_and_append(
            project.git_commit_registry, change._annotated_lines,
            change.annotated_lines, "annotated line commit",
        )
        if change._parent_change is not None:
            change.parent_change = _resolve_single(
                project.change_registry, change._parent_change, "parent change",
            )
        del change._commit, change._file, change._parent_commit
        del change._annotated_lines, change._parent_change
