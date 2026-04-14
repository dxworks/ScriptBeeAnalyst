from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, List, Collection

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.common.base_models import Project, Account
from src.common.identity import identity_fields
from src.common.relinking import relink_git_objects
from src.inspector_git.linker.registry import AccountRegistry, CommitRegistry, FileRegistry, ChangeRegistry
from src.inspector_git.utils.constants import DEV_NULL
from src.logger import get_logger

LOG = get_logger(__name__)


# ── Enums ───────────────────────────────────────────────────────────────────────

class LineOperation(Enum):
    ADD = "ADD"
    DELETE = "DELETE"


class ChangeType(Enum):
    ADD = "ADD"
    DELETE = "DELETE"
    RENAME = "RENAME"
    MODIFY = "MODIFY"


# ── Value Objects ───────────────────────────────────────────────────────────────

class GitAccountId(BaseModel):
    model_config = ConfigDict(frozen=True)
    email: str
    name: str

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"


@identity_fields("operation", "line_number")
class LineChange(BaseModel):
    operation: LineOperation
    line_number: int
    commit: GitCommit


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
            self.line_changes == other.line_changes
            and self.deleted_lines == other.deleted_lines
            and self.added_lines == other.added_lines
        )


# ── Entities ────────────────────────────────────────────────────────────────────

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
                if "name" not in data:
                    data["name"] = git_id.name
            if git_project is not None:
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


class File(BaseModel):
    is_binary: bool
    project: Optional[GitProject] = None
    changes: List[Change] = Field(default_factory=list)
    id: uuid.UUID = Field(default_factory=uuid.uuid4)

    class Config:
        arbitrary_types_allowed = True

    def __reduce__(self):
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
        return str(self.changes[-1].new_file_name if self.changes else "<unknown>")


class GitCommit(BaseModel):
    project: Optional[GitProject] = None
    id: str
    message: str
    author_date: datetime
    committer_date: datetime
    author: Optional[GitAccount] = None
    committer: Optional[GitAccount] = None
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
        threshold = other.committer_date - age
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
    commit: Optional[GitCommit] = None
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
            commit=None,
            change_type=change_type,
            old_file_name=old_file_name,
            new_file_name=new_file_name,
            file=None,
            parent_commit=None,
            hunks=hunks,
            annotated_lines=[],
            parent_change=None,
            compute_annotated_lines=compute_annotated_lines,
        )
        obj._commit = commit_id
        obj._file = file_id
        obj._parent_commit = parent_commit_id
        obj._annotated_lines = annotated_line_ids
        obj._parent_change = parent_change_id

        return obj

    def _apply_line_changes(self, parent_change: Optional[Change]) -> None:
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
        return hash((
            self.change_type,
            self.file,
            tuple(self.line_changes),
            tuple(self.annotated_lines),
        ))

    def __str__(self) -> str:
        return (f"In {self.commit.id} : {self.commit.message}\n"
                f"{self.change_type} {self.old_file_name}->{self.new_file_name}")


# ── Project ─────────────────────────────────────────────────────────────────────

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
        relink_git_objects(self)

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
        obj = cls(
            name=name,
            account_registry=AccountRegistry(),
            git_commit_registry=CommitRegistry(),
            file_registry=FileRegistry(),
            change_registry=ChangeRegistry(),
        )

        obj.account_registry.add_all(accounts)
        obj.git_commit_registry.add_all(commits)
        obj.file_registry.add_all(files)
        obj.change_registry.add_all(changes)

        obj._relink_objects()

        return obj


# ── Pickle compatibility ────────────────────────────────────────────────────────
# These classes use __reduce__/_rebuild for serialization. Pickle encodes the
# module path, so we keep it pointing to src.common.models (the re-export hub)
# to ensure old .pkl files can still be deserialized.

for _cls in (GitAccount, File, GitCommit, Change, GitProject):
    _cls.__module__ = "src.common.models"
