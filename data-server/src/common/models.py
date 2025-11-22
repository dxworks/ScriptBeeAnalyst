from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Type, TypeVar, List, Collection
from pydantic import BaseModel, Field, model_validator

from src.inspector_git.linker.registry import AccountRegistry, CommitRegistry, FileRegistry, ChangeRegistry
from src.github_miner.linker.registries import GitHubUserRegistry, PullRequestRegistry, GitHubCommitRegistry
from src.jira_miner.linker.registries import IssueStatusCategoryRegistry, IssueStatusRegistry, IssueTypeRegistry, IssueRegistry, JiraUserRegistry

from src.inspector_git.utils.constants import DEV_NULL

from src.logger import get_logger

LOG = get_logger(__name__)

class Project(BaseModel, ABC):
    linked_projects: List[Project] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

    def link(self, other: Project) -> None:
        if other not in self.linked_projects:
            self.linked_projects.append(other)
        else:
            LOG.warning(f"Project {other} is already linked to {self}")

    def is_linked(self, other: Project) -> bool:
        return other in self.linked_projects

class Account(BaseModel, ABC):
    name: str
    project: Optional[Project] = None
    developer: Optional[Developer] = None

    class Config:
        arbitrary_types_allowed = True

    @property
    @abstractmethod
    def id(self) -> str:
        ...

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if not isinstance(other, Account):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

AccountType = TypeVar("AccountType", bound=Account)
class Developer(BaseModel):
    name: str
    accounts: list[Account] = Field(default_factory=list)

    def get_accounts_of_type(self, account_type: Type[AccountType]) -> list[AccountType]:
        return [account for account in self.accounts if isinstance(account, account_type)]

    class Config:
        arbitrary_types_allowed = True

Account.model_rebuild()



class GitAccountId(BaseModel):
    email: str
    name: str

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"

class GitAccount(Account):
    git_id: GitAccountId
    commits: List[GitCommit] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def set_account_fields(cls, data: dict):
        """
        Ensure that 'name' and 'project' are properly set
        based on git_id and git_project.
        """
        if isinstance(data, dict):
            git_id = data.get("git_id")
            git_project = data.get("project") or data.get("git_project")

            if git_id is not None:
                # set the inherited 'name' from git_id
                if "name" not in data:
                    data["name"] = git_id.name
            if git_project is not None:
                # normalize: accept 'git_project' as 'project'
                data["project"] = git_project

        return data

    @property
    def id(self) -> str:
        return str(self.git_id)

    @property
    def changes(self) -> List[Change]:
        return [change for commit in self.commits for change in commit.changes]

    @property
    def files(self) -> List[File]:
        return list({change.file for change in self.changes})

    def __reduce__(self):
        state = (self.git_id, [c.id for c in self.commits])
        return self._rebuild, state

    @classmethod
    def _rebuild(cls, id: GitAccountId, commits: List[str]):
        obj = cls(git_id=id, name=id.name)
        obj._commits = commits
        return obj

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if not isinstance(other, GitAccount):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

    def __str__(self) -> str:
        return str(self.git_id)

class GitProject(Project):
    name: str
    account_registry: AccountRegistry = Field(default_factory=AccountRegistry)
    git_commit_registry: CommitRegistry = Field(default_factory=CommitRegistry)
    file_registry: FileRegistry = Field(default_factory=FileRegistry)
    change_registry: ChangeRegistry = Field(default_factory=ChangeRegistry)

    class Config:
        arbitrary_types_allowed = True

    def __str__(self):
        return (
            f"account reg: {len(self.account_registry.all)},\n"
            f"commit reg: {len(self.git_commit_registry.all)},\n"
            f"file reg: {len(self.file_registry.all)},\n"
            f"change reg: {len(self.change_registry.all)}"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GitProject):
            return NotImplemented
        return (
                self.name == other.name
                and self.account_registry._map == other.account_registry._map
                and self.git_commit_registry._map == other.git_commit_registry._map
                and self.file_registry._map == other.file_registry._map
                and self.change_registry._map == other.change_registry._map
        )

    def _relink_objects(self):
        for account in self.account_registry.all:
            for c in account._commits:
                commit = self.git_commit_registry.get_by_id(c)
                if commit is None:
                    LOG.warning(f"Could not find commit {c} in commit registry")
                account.commits.append(commit)
        del account._commits
        account.project = self

        for commit in self.git_commit_registry.all:
            author = self.account_registry.get_by_id(commit._author.__str__())
            if author is None:
                LOG.warning(f"Could not find author {commit._author} in account registry")
            commit.author = author

            committer = self.account_registry.get_by_id(commit._committer.__str__())
            if committer is None:
                LOG.warning(f"Could not find committer {commit._committer} in account registry")
            commit.committer = committer

            for p in commit._parents:
                parent = self.git_commit_registry.get_by_id(p)
                if parent is None:
                    LOG.warning(f"Could not find parent {p} in commit registry")
                commit.parents.append(parent)

            for c in commit._children:
                child = self.git_commit_registry.get_by_id(c)
                if child is None:
                    LOG.warning(f"Could not find child {c} in commit registry")
                commit.children.append(child)

            for c in commit._changes:
                change = self.change_registry.get_by_id(c)
                if change is None:
                    LOG.warning(f"Could not find change {c} in change registry")
                commit.changes.append(change)

            del commit._author
            del commit._committer
            del commit._parents
            del commit._children
            del commit._changes

            commit.project = self


        for file in self.file_registry.all:
            for c in file._changes:
                change = self.change_registry.get_by_id(c)
                if change is None:
                    LOG.warning(f"Could not find change {c} in change registry")
                file.changes.append(change)

            del file._changes

            file.project = self

        for change in self.change_registry.all:
            commit = self.git_commit_registry.get_by_id(change._commit) # should field _commit must exist
            if commit is None:
                LOG.warning(f"Could not find commit {change._commit} in commit registry")
            change.commit = commit

            file = self.file_registry.get_by_id(change._file) # should field _file must exist
            if file is None:
                LOG.warning(f"Could not find file {change._file} in file registry")
            change.file = file

            if change._parent_commit is not None:
                parent_commit = self.git_commit_registry.get_by_id(change._parent_commit)
                if parent_commit is None:
                    LOG.warning(f"Could not find parent commit {change._parent_commit} in commit registry")
                change.parent_commit = parent_commit

            for c in change._annotated_lines:
                commit = self.git_commit_registry.get_by_id(c)
                if commit is None:
                    LOG.warning(f"Could not find commit {c} in commit registry")
                change.annotated_lines.append(commit)

            if change._parent_change is not None:
                parent_change = self.change_registry.get_by_id(change._parent_change)
                if parent_change is None:
                    LOG.warning(f"Could not find parent change {change._parent_change} in change registry")
                change.parent_change = parent_change

            del change._commit
            del change._file
            del change._parent_commit
            del change._annotated_lines
            del change._parent_change

    def __reduce__(self):
        state = (
            self.name,
            list(self.account_registry.all),
            list(self.git_commit_registry.all),
            list(self.file_registry.all),
            list(self.change_registry.all),
        )
        return self._rebuild, state

    @classmethod
    def _rebuild(
        cls,
        name: str,
        accounts: Collection[GitAccount],
        commits: Collection[GitCommit],
        files: Collection[File],
        changes: Collection[Change],
    ):
        # Create empty registries
        obj = cls(
            name=name,
            account_registry=AccountRegistry(),
            git_commit_registry=CommitRegistry(),
            file_registry=FileRegistry(),
            change_registry=ChangeRegistry(),
        )

        # Fill registries from the saved collections
        obj.account_registry.add_all(accounts)
        obj.git_commit_registry.add_all(commits)
        obj.file_registry.add_all(files)
        obj.change_registry.add_all(changes)

        obj._relink_objects()

        return obj

class LineOperation(Enum):
    ADD = "ADD"
    DELETE = "DELETE"

class ChangeType(Enum):
    ADD = "ADD"
    DELETE = "DELETE"
    RENAME = "RENAME"
    MODIFY = "MODIFY"

class LineChange(BaseModel):
    operation: LineOperation
    line_number: int
    commit: GitCommit

    def __eq__(self, other):
        if not isinstance(other, LineChange):
            return False
        return (
                self.operation == other.operation and
                self.line_number == other.line_number
        )

    def __hash__(self):
        # Only use immutable fields
        return hash((self.operation, self.line_number))

class Hunk(BaseModel):
    line_changes: List[LineChange]
    deleted_lines: List[LineChange] = []
    added_lines: List[LineChange] = []

    @model_validator(mode="after")
    @classmethod
    def derive_added_deleted(cls, values):
        line_changes = values.line_changes
        values.deleted_lines = [lc for lc in line_changes if lc.operation == LineOperation.DELETE]
        values.added_lines = [lc for lc in line_changes if lc.operation == LineOperation.ADD]
        return values
    def __hash__(self):
        return hash((
            tuple(self.line_changes),
            tuple(self.deleted_lines),
            tuple(self.added_lines),
        ))

    def __eq__(self, other):
        if not isinstance(other, Hunk):
            return False
        return (
                self.line_changes == other.line_changes and
                self.deleted_lines == other.deleted_lines and
                self.added_lines == other.added_lines
        )

class File(BaseModel):
    is_binary: bool
    project: Optional[GitProject] = None
    changes: List[Change] = Field(default_factory=list)
    id: uuid.UUID = Field(default_factory=uuid.uuid4)

    class Config:
        arbitrary_types_allowed = True

    def __reduce__(self):
        # Instead of storing Student objects, store only IDs
        state = (self.is_binary,
                 [c.id for c in self.changes],
                 self.id)
        return self._rebuild, state

    @classmethod
    def _rebuild(
            cls,
            is_binary: bool,
            change_ids: List[uuid.UUID],
            id: uuid.UUID,
    ):
        obj = cls(
            is_binary=is_binary,
            changes=[],
            id=id,
        )
        obj._changes = change_ids
        return obj

    def is_alive(self, commit: Optional[GitCommit] = None) -> bool:
        last = self.get_last_change(commit)
        typ = last.change_type if last is not None else None
        return typ is not None and typ is not ChangeType.DELETE

    def annotated_lines(self, commit: Optional[GitCommit] = None) -> List[GitCommit]:
        last = self.get_last_change(commit)
        return last.annotated_lines if (last is not None and getattr(last, "annotated_lines", None) is not None) else []

    def full_path(self, commit: Optional[GitCommit] = None) -> Optional[str]:
        last = self.get_last_change(commit)
        if last is None:
            return None
        new_file_name = getattr(last, "new_file_name", None)
        return f"{self.project.name}/{new_file_name}" if new_file_name is not None else None

    def file_name(self, commit: Optional[GitCommit] = None) -> Optional[str]:
        rel = self.relative_path(commit)
        if rel is None:
            return None
        if rel == DEV_NULL:
            return rel
        idx = rel.rfind("/")
        return rel if idx == -1 else rel[idx + 1 :]

    def relative_path(self, commit: Optional[GitCommit] = None) -> Optional[str]:
        last = self.get_last_change(commit)
        return getattr(last, "new_file_name", None) if last is not None else None

    def last_existing_name(self, commit: Optional[GitCommit] = None) -> Optional[str]:
        for change in reversed(self.changes):
            if change.new_file_name != DEV_NULL:
                return change.new_file_name

        if self.changes:
            return self.changes[-1].new_file_name

        LOG.warning(f"Could not find last existing name for file {self.id}")
        return None

    def get_last_change(self, commit: Optional[GitCommit] = None) -> Optional[Change]:
        if not self.changes:
            return None
        if commit is None:
            return self.changes[-1]
        return self._get_last_change_recursively(commit)

    def _get_last_change_recursively(self, commit: GitCommit) -> Optional[Change]:
        found = next((c for c in self.changes if getattr(c, "commit", None) == commit), None)
        if found is not None:
            return found
        parents = getattr(commit, "parents", None)
        if not parents:
            return None
        parent = parents[0] if len(parents) > 0 else None
        if parent is None:
            return None
        return self._get_last_change_recursively(parent)

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if not isinstance(other, File):
            return False
        return self.is_binary == other.is_binary and self.id == other.id

    def __hash__(self) -> int:
        result = hash(self.is_binary)
        result = 31 * result + hash(self.id)
        return result

    def __str__(self) -> str:
        return str(self.changes[-1].new_file_name if self.changes else "nu stiu")

class GitCommit(BaseModel):
    project: Optional[GitProject] = None
    id: str
    message: str
    author_date: datetime
    committer_date: datetime
    author: Optional[GitAccount] = None # this is optional to not cause problems in the loading process after serialization
    committer: Optional[GitAccount] = None # same as above
    parents: List[GitCommit] = Field(default_factory=list)
    children: List[GitCommit] = Field(default_factory=list)
    changes: List[Change] = Field(default_factory=list)
    branch_id: int = 0
    repo_size: int = 0

    issues: List[Issue] = Field(default_factory=list)
    pull_requests: List[PullRequest] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

    def older_than(self, age: timedelta, other: GitCommit) -> bool:
        try:
            threshold = other.committer_date - age
        except Exception:
            # If subtraction fails, propagate the error so callers know the type is incompatible.
            raise
        return self.committer_date < threshold

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if not isinstance(other, GitCommit):
            return False
        return (
            self.id == other.id
            and self.message == other.message
            and self.author_date == other.author_date
            and self.committer_date == other.committer_date
            and self.author == other.author
            and self.committer == other.committer
        )

    def __hash__(self) -> int:
        result = hash(self.id)
        result = 31 * result + hash(self.message)
        result = 31 * result + hash(self.author_date)
        result = 31 * result + hash(self.committer_date)
        result = 31 * result + hash(self.author)
        result = 31 * result + hash(self.committer)
        return result

    @property
    def is_merge_commit(self) -> bool:
        return len(self.parents) > 1

    @property
    def is_split_commit(self) -> bool:
        return len(self.children) > 1

    def __reduce__(self):
        state = (self.id,
                 self.message,
                 self.author_date,
                 self.committer_date,
                 self.author.git_id,
                 self.committer.git_id,
                 [p.id for p in self.parents],
                 [c.id for c in self.children],
                 [c.id for c in self.changes],
                 self.branch_id,
                 self.repo_size,)
        return self._rebuild, state

    @classmethod
    def _rebuild(
            cls,
            id: str,
            message: str,
            author_date: datetime,
            committer_date: datetime,
            author_id: GitAccountId,
            committer_id: GitAccountId,
            parent_ids: List[str],
            child_ids: List[str],
            change_ids: List[str],
            branch_id: int,
            repo_size: int,
    ):
        obj = cls(
            id=id,
            message=message,
            author_date=author_date,
            committer_date=committer_date,
            author=None,
            committer=None,
            parents=[],
            children=[],
            changes=[],
            branch_id=branch_id,
            repo_size=repo_size,
        )
        # Store IDs temporarily for later linking
        obj._author = author_id
        obj._committer = committer_id
        obj._parents = parent_ids
        obj._children = child_ids
        obj._changes = change_ids

        return obj

    def add_child(self, commit: GitCommit) -> None:
        self.children = [*self.children, commit]

    def is_after_in_tree(self, other: GitCommit) -> bool:
        if other in self.parents:
            return True
        return any(parent.is_after_in_tree(other) for parent in self.parents)

    def __str__(self) -> str:
        return self.id

class Change(BaseModel):
    id: str
    commit: Optional[GitCommit] = None # to not cause errors during loading after serialization
    change_type: ChangeType
    old_file_name: str
    new_file_name: str
    file: Optional[File] = None
    parent_commit: Optional[GitCommit] = None
    hunks: List[Hunk] = Field(default_factory=list)
    annotated_lines: List[GitCommit] = Field(default_factory=list)
    parent_change: Optional[Change] = None
    compute_annotated_lines: bool = False

    class Config:
        arbitrary_types_allowed = True

    @model_validator(mode="before")
    @classmethod
    def set_id(cls, values):
        if values.get("id") is None:
            commit = values.get("commit")
            old_name = values.get("old_file_name")
            new_name = values.get("new_file_name")
            if commit and old_name and new_name:
                values["id"] = f"{commit.id}-{old_name}->{new_name}"
        return values

    @property
    def line_changes(self) -> List[LineChange]:
        return [lc for hunk in self.hunks for lc in hunk.line_changes]

    @property
    def deleted_lines(self) -> List[LineChange]:
        return [lc for hunk in self.hunks for lc in hunk.deleted_lines]

    @property
    def added_lines(self) -> List[LineChange]:
        return [lc for hunk in self.hunks for lc in hunk.added_lines]

    @model_validator(mode="after")
    @classmethod
    def apply_line_changes(cls, model: "Change") -> "Change":
        if model.compute_annotated_lines and not model.file.is_binary:
            model._apply_line_changes(model.parent_change)
        return model

    def __reduce__(self):
        state = (self.id,
                 self.commit.id,
                 self.change_type,
                 self.old_file_name,
                 self.new_file_name,
                 self.file.id,
                 self.parent_commit.id if self.parent_commit else None,
                 self.hunks,
                 [a.id for a in self.annotated_lines],
                 self.parent_change.id if self.parent_change else None,
                 self.compute_annotated_lines)
        return self._rebuild, state

    @classmethod
    def _rebuild(
            cls,
            id: str,
            commit_id: str,
            change_type: ChangeType,
            old_file_name: str,
            new_file_name: str,
            file_id: uuid.UUID,
            parent_commit_id: Optional[str],
            hunks: list,
            annotated_line_ids: List[str],
            parent_change_id: Optional[str],
            compute_annotated_lines: bool,
    ):
        obj = cls(
            id=id,
            commit=None,  # will attach later
            change_type=change_type,
            old_file_name=old_file_name,
            new_file_name=new_file_name,
            file=None,  # will attach later
            parent_commit=None,
            hunks=hunks,
            annotated_lines=[],
            parent_change=None,
            compute_annotated_lines=compute_annotated_lines,
        )
        # Store IDs for later linking
        obj._commit = commit_id
        obj._file = file_id
        obj._parent_commit = parent_commit_id
        obj._annotated_lines = annotated_line_ids
        obj._parent_change = parent_change_id

        return obj

    def _apply_line_changes(self, parent_change: Optional["Change"]) -> None:
        try:
            new_annotated_lines = list(parent_change.annotated_lines) if parent_change else []
            deletes = self.deleted_lines
            adds = self.added_lines
            for d in sorted(deletes, key=lambda x: x.line_number, reverse=True):
                new_annotated_lines.pop(d.line_number - 1)
            for a in adds:
                new_annotated_lines.insert(a.line_number - 1, a.commit)
            self.annotated_lines = new_annotated_lines
        except IndexError:
            self.file.is_binary = True


    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if not isinstance(other, Change):
            return False
        return (
                self.change_type == other.change_type
                and self.file == other.file
                and self.line_changes == other.line_changes
                and self.annotated_lines == other.annotated_lines
        )

    def __hash__(self) -> int:
        result = hash(self.change_type)
        result = 31 * result + hash(self.commit)
        result = 31 * result + hash(self.old_file_name)
        result = 31 * result + hash(self.new_file_name)
        result = 31 * result + hash(tuple(self.hunks))
        result = 31 * result + hash(tuple(self.annotated_lines))
        return result

    def __str__(self) -> str:
        return (f"In {self.commit.id} : {self.commit.message}\n"
                f"{self.change_type} {self.old_file_name}->{self.new_file_name}")





class IssueStatusCategory(BaseModel):
    key: str
    name: str

    issue_statuses: List["IssueStatus"] = Field(default_factory=list)

    def __eq__(self, other):
        if not isinstance(other, IssueStatusCategory):
            return False
        return (self.key, self.name) == (other.key, other.name)

    def __hash__(self):
        return hash((self.key, self.name))

class IssueStatus(BaseModel):
    id: str
    name: str

    issue_status_categories: "IssueStatusCategory" = Field(default_factory=IssueStatusCategory)
    issues: List["Issue"] = Field(default_factory=list)

    def __eq__(self, other):
        if not isinstance(other, IssueStatus):
            return False
        return (self.id, self.name) == (other.id, other.name)

    def __hash__(self):
        return hash((self.id, self.name))

class IssueType(BaseModel):
    id: str
    name: str
    description: str
    isSubTask: bool

    issues: List["Issue"] = Field(default_factory=list)

    def __eq__(self, other):
        if not isinstance(other, IssueType):
            return False
        return (self.id, self.name, self.description, self.isSubTask) == (
            other.id,
            other.name,
            other.description,
            other.isSubTask,
        )

    def __hash__(self):
        return hash((self.id, self.name, self.description, self.isSubTask))

class Issue(BaseModel):
    id: int
    key: str
    summary: str
    createdAt: datetime
    updatedAt: datetime

    issue_statuses: List["IssueStatus"] = Field(default_factory=list)
    issue_types: List["IssueType"] = Field(default_factory=list)
    creator: Optional["JiraUser"] = None
    jira_users_as_assignee: List["JiraUser"] = Field(default_factory=list)
    reporter: Optional["JiraUser"] = None
    parent: Optional["Issue"] = None
    children: List["Issue"] = Field(default_factory=list)

    git_commits: List[GitCommit] = Field(default_factory=list)
    pull_requests: List[PullRequest] = Field(default_factory=list)


    def __eq__(self, other):
        if not isinstance(other, Issue):
            return False
        return (
            self.id,
            self.key,
            self.summary,
            self.createdAt,
            self.updatedAt,
        ) == (
            other.id,
            other.key,
            other.summary,
            other.createdAt,
            other.updatedAt,
        )

    def __hash__(self):
        return hash((self.id, self.key, self.summary, self.createdAt, self.updatedAt))

class JiraUser(BaseModel):
    key: str
    name: str
    link: str

    issues_as_reporter: List["Issue"] = Field(default_factory=list)
    issues_as_creator: List["Issue"] = Field(default_factory=list)
    issues_as_assignee: List["Issue"] = Field(default_factory=list)

    def __eq__(self, other):
        if not isinstance(other, JiraUser):
            return False
        return (self.key, self.name, self.link) == (other.key, other.name, other.link)

    def __hash__(self):
        return hash((self.key, self.name, self.link))

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





class GitHubUser(BaseModel):
    url: str
    login: Optional[str]
    name: Optional[str]

    pull_requests_as_creator: List["PullRequest"] = Field(default_factory=list)
    pull_requests_as_merged_by: List["PullRequest"] = Field(default_factory=list)
    pull_requests_as_assignee: List["PullRequest"] = Field(default_factory=list)

    def __eq__(self, other):
        if not isinstance(other, GitHubUser):
            return False
        return (self.url, self.login, self.name) == (other.url, other.login, other.name)

    def __hash__(self):
        return hash((self.url, self.login, self.name))

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


    def __eq__(self, other):
        if not isinstance(other, PullRequest):
            return False
        return (
            self.number,
            self.title,
            self.state,
            self.changedFiles,
            self.body,
            self.createdAt,
            self.mergedAt,
            self.closedAt,
            self.updatedAt,
        ) == (
            other.number,
            other.title,
            other.state,
            other.changedFiles,
            other.body,
            other.createdAt,
            other.mergedAt,
            other.closedAt,
            other.updatedAt,
        )

    def __hash__(self):
        return hash((
            self.number,
            self.title,
            self.state,
            self.changedFiles,
            self.body,
            self.createdAt,
            self.mergedAt,
            self.closedAt,
            self.updatedAt,
        ))

class GitHubCommit(BaseModel):
    id: str
    date: datetime
    message: str
    changedFiles: int

    pull_requests: List[PullRequest] = Field(default_factory=list)

    def __eq__(self, other):
        if not isinstance(other, GitHubCommit):
            return False
        return (self.id, self.date, self.message, self.changedFiles) == (
            other.id,
            other.date,
            other.message,
            other.changedFiles,
        )

    def __hash__(self):
        return hash((self.id, self.date, self.message, self.changedFiles))

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
            and self.pull_request_registry._map  == other.pull_request_registry._map
            and self.git_hub_commit_registry._map  == other.git_hub_commit_registry._map
        )





LineChange.model_rebuild()
Hunk.model_rebuild()
GitAccountId.model_rebuild()
File.model_rebuild()
GitCommit.model_rebuild()
Change.model_rebuild()

IssueStatusCategory.model_rebuild()
IssueStatus.model_rebuild()
IssueType.model_rebuild()
Issue.model_rebuild()
JiraUser.model_rebuild()

GitHubUser.model_rebuild()
PullRequest.model_rebuild()
GitHubCommit.model_rebuild()
GitHubProject.model_rebuild()