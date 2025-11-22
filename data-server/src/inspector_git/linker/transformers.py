from abc import ABC, abstractmethod
from collections import deque
from typing import Optional, List
from src.inspector_git.linker.exceptions import NoChangeException
from src.common.models import GitAccountId, GitAccount, GitProject, LineOperation, ChangeType, LineChange, Hunk, File, \
    GitCommit, Change
from src.inspector_git.reader.dto.gitlog.chnage_dto import ChangeDTO
from src.inspector_git.reader.dto.gitlog.commit_dto import CommitDTO
from src.inspector_git.reader.dto.gitlog.git_log_dto import GitLogDTO
from src.inspector_git.reader.enums.chnage_type import ChangeType as ChangeTypeDTO
from datetime import datetime
from src.inspector_git.utils.constants import parse_commit_date
from src.logger import get_logger

LOG = get_logger(__name__)

class ChangeFactory(ABC):
    @abstractmethod
    def create(
        self,
        commit: GitCommit,
        change_type: ChangeType,
        old_file_name: str,
        new_file_name: str,
        file: File,
        parent_commit: Optional[GitCommit],
        hunks: List[Hunk],
        parent_change: Optional[Change],
        compute_annotated_lines: bool
    ) -> Change:
        pass

class SimpleChangeFactory(ChangeFactory):
    def create(
        self,
        commit: GitCommit,
        change_type: ChangeType,
        old_file_name: str,
        new_file_name: str,
        file: File,
        parent_commit: Optional[GitCommit],
        hunks: List[Hunk],
        parent_change: Optional[Change],
        compute_annotated_lines: bool
    ) -> Change:
        return Change(
            commit=commit,
            change_type=change_type,
            old_file_name=old_file_name,
            new_file_name=new_file_name,
            file=file,
            parent_commit=parent_commit,
            hunks=hunks,
            parent_change=parent_change,
            compute_annotated_lines=compute_annotated_lines
        )

class ChangeTransformer:
    @staticmethod
    def get_last_change(parent_commit: GitCommit, file_name: str) -> Change:
        stack = [parent_commit]

        while stack:
            commit = stack.pop()

            found = next((c for c in commit.changes if c.new_file_name == file_name), None)
            if found is not None:
                return found

            if commit.parents:
                stack.extend(reversed(commit.parents))  # preserve order

        raise NoChangeException(file_name)

    @staticmethod
    def transform(
        change_dto: ChangeDTO,
        commit: GitCommit,
        project: GitProject,
        compute_annotated_lines: bool,
        change_factory: ChangeFactory,
    ) -> Optional[Change]:
        """
        Transform a ChangeDTO into a domain Change using provided change_factory.
        Returns None if the change cannot be transformed (e.g., NoChangeException).
        """
        # resolve parent commit (None if empty)
        parent_commit: Optional[GitCommit]
        if not change_dto.parent_commit_id:
            parent_commit = None
        else:
            parent_commit = next((p for p in commit.parents if p.id == change_dto.parent_commit_id), None)


        try:
            if change_dto.type == ChangeTypeDTO.ADD:
                last_change: Optional[Change] = None
            else:
                if parent_commit is None:
                    raise NoChangeException(change_dto.old_file_name)
                last_change = ChangeTransformer.get_last_change(parent_commit, change_dto.old_file_name)
        except NoChangeException as e:
            LOG.error("Change not found for file!", exc_info=e)
            return None

        LOG.debug(
            "Creating %s change for file: %s -> %s",
            getattr(change_dto, "type").name,
            getattr(change_dto, "old_file_name"),
            getattr(change_dto, "new_file_name"),
        )

        file_for_change = ChangeTransformer._get_file_for_change(change_dto, last_change, project)
        hunks = ChangeTransformer._get_hunks(last_change, change_dto, commit)

        if project.file_registry.get_by_id(file_for_change.id) is None:
            LOG.warning(
                "File %s not found in project. This may be due to a bug in the git log reader.",
                file_for_change.id,
            )

        # print(file_for_change.id)
        # for file in project.file_registry.all:
        #     print(file.id, end=", ")
        # print()



        change =  change_factory.create(
            commit=commit,
            change_type=ChangeType[getattr(change_dto, "type").name],
            old_file_name=getattr(change_dto, "old_file_name"),
            new_file_name=getattr(change_dto, "new_file_name"),
            file=file_for_change,
            parent_commit=parent_commit,
            hunks=hunks,
            parent_change=last_change,
            compute_annotated_lines=compute_annotated_lines,
        )
        project.change_registry.add(change)
        return change

    @staticmethod
    def _get_hunks(last_change: Optional[Change], change_dto: ChangeDTO, commit: GitCommit) -> List[Hunk]:
        LOG.debug("Calculating line changes")
        if last_change is not None and last_change.file.is_binary:
            return []
        dto_hunks = change_dto.hunks
        result_hunks: List[Hunk] = []
        for dto_hunk in dto_hunks:
            dto_line_changes = dto_hunk.line_changes
            line_changes = [
                LineChange(
                    operation=LineOperation[lc.operation.name],
                    line_number=lc.number,
                    commit=commit,
                )
                for lc in dto_line_changes
            ]
            result_hunks.append(Hunk(line_changes=line_changes))
        return result_hunks

    @staticmethod
    def _get_file_for_change(change_dto: ChangeDTO, last_change: Optional[Change], project: GitProject) -> File:
        LOG.debug("Getting file")
        if change_dto.type == ChangeTypeDTO.ADD:
            new_file = File(is_binary=change_dto.is_binary, project=project)
            project.file_registry.add(new_file)
            return new_file
        else:
            if not last_change:
                LOG.warning("No last change found for file %s", change_dto.old_file_name)
            return last_change.file

class MergeChangesTransformer:
    @staticmethod
    def transform(
        change_dtos: List[ChangeDTO],
        commit: GitCommit,
        project: GitProject,
        compute_annotated_lines: bool,
        change_factory: ChangeFactory,
    ) -> List[Change]:
        changes = [
            c
            for dto in change_dtos
            if (c := ChangeTransformer.transform(dto, commit, project, compute_annotated_lines, change_factory))
            is not None
        ]
        if not changes:
            return []
        return MergeChangesTransformer._fix_changes(changes, commit, project)

    @staticmethod
    def _fix_changes(changes: List[Change], commit: GitCommit, project: GitProject) -> List[Change]:
        LOG.debug("Merging %s changes", len(changes))

        missing_change: Optional[Change] = None
        if len(changes) < len(commit.parents) and not all(c.change_type == ChangeType.DELETE for c in changes):
            missing_change = MergeChangesTransformer._get_missing_change(changes, commit)

        MergeChangesTransformer._fix_annotated_lines_commits(changes, missing_change, commit)
        MergeChangesTransformer._merge_files(changes, missing_change, project)

        LOG.debug("Finished merging changes")
        return changes

    @staticmethod
    def _get_missing_change(changes: List[Change], commit: GitCommit) -> Change:
        clean_parent = next(
            (p for p in commit.parents if all(c.parent_commit != p for c in changes)),
            None,
        )
        return ChangeTransformer.get_last_change(clean_parent, changes[0].new_file_name)

    @staticmethod
    def _merge_files(changes: List[Change], missing_change: Optional[Change], project: GitProject) -> None:
        files = list({c.file for c in changes} | ({missing_change.file} if missing_change else set()))
        if len(files) > 1:
            all_file_changes = list({ch for f in files for ch in f.changes})
            file = files[0]
            file.changes = sorted(all_file_changes, key=lambda c: c.commit.committer_date)
            for c in changes:
                c.file = file
            for f in files[1:]:
                # print(f"before deleting a file print:{f.id}")
                for ch in f.changes:
                    ch.file = file
                    if ch not in file.changes:
                        file.changes.append(ch)

                #DEBUG
                for ch in project.change_registry.all:
                    if ch.file == f:
                        ch.file = file
                        LOG.debug("Found change link to file for deletion outside the file changes list: %s", ch.id)

                project.file_registry.delete(f)

    @staticmethod
    def _fix_annotated_lines_commits(changes: List[Change], missing_change: Optional[Change], commit: GitCommit) -> None:
        if missing_change is not None:
            changes[0].annotated_lines = list(missing_change.annotated_lines)

        annotated_files = [c.annotated_lines for c in changes]
        if not annotated_files or not annotated_files[0]:
            return

        for i in range(len(annotated_files[0])):
            current_annotated_lines = [af[i] for af in annotated_files if i < len(af)]
            if not current_annotated_lines:
                continue
            first_line = current_annotated_lines[0]
            rest_lines = current_annotated_lines[1:]
            if first_line == commit:
                replacement = next((line for line in rest_lines if line != commit), None)
                if replacement:
                    annotated_files[0][i] = replacement

        for c in changes[1:]:
            c.annotated_lines = list(changes[0].annotated_lines)

class CommitTransformer:
    @staticmethod
    def add_to_project(
        commit_dto: CommitDTO,
        project: GitProject,
        compute_annotated_lines: bool,
        change_factory: ChangeFactory = SimpleChangeFactory(),
    ) -> None:
        LOG.debug("Creating commit with id: %s", commit_dto.id)
        parents = CommitTransformer._get_parents_from_ids(commit_dto.parent_ids, project)
        if len(parents) > 1:
            LOG.debug("Is merge commit")

        author = CommitTransformer._get_author(commit_dto, project)
        LOG.debug("Parsed author %s", author.id)

        committer = (
            author
            if not commit_dto.committer_name
            else CommitTransformer._get_committer(commit_dto, project)
        )
        LOG.debug("Parsed committer %s", committer.id)

        author_date = CommitTransformer._parse_date(commit_dto.author_date)
        committer_date = (
            author_date
            if not commit_dto.committer_date
            else CommitTransformer._parse_date(commit_dto.committer_date)
        )

        commit = GitCommit(
            project=project,
            id=commit_dto.id,
            message=commit_dto.message,
            author_date=author_date,
            committer_date=committer_date,
            author=author,
            committer=committer,
            parents=parents,
            children=[],
            changes=[],
        )

        for p in commit.parents:
            p.add_child(commit)

        LOG.debug("Adding commit to repository and to authors")
        project.git_commit_registry.add(commit)
        author.commits.append(commit)
        if committer != author:
            committer.commits.append(commit)

        CommitTransformer._add_changes_to_commit(
            commit_dto.changes, commit, project, compute_annotated_lines, change_factory
        )

        commit.repo_size = CommitTransformer._get_parent_commit_size(commit) + CommitTransformer._compute_commit_growth(
            commit
        )

        LOG.debug("Done creating commit with id: %s", commit_dto.id)

    @staticmethod
    def _compute_commit_growth(commit: GitCommit) -> int:
        return sum(
            len(ch.added_lines) - len(ch.deleted_lines)
            for ch in commit.changes
            if not commit.parents or ch.parent_commit == commit.parents[0]
        )

    @staticmethod
    def _get_parent_commit_size(commit: GitCommit) -> int:
        return commit.parents[0].repo_size if commit.parents else 0

    @staticmethod
    def _parse_date(timestamp: str) -> datetime:
        LOG.debug("Parsing date: %s", timestamp)
        return parse_commit_date(timestamp)

    @staticmethod
    def _add_changes_to_commit(
        changes: List[ChangeDTO],
        commit: GitCommit,
        project: GitProject,
        compute_annotated_lines: bool,
        change_factory: ChangeFactory,
    ) -> None:
        LOG.debug("Filtering changes")
        if commit.is_merge_commit:
            changes_by_file = {}
            for ch in changes:
                key = ch.old_file_name if ch.type == ChangeTypeDTO.DELETE else ch.new_file_name
                changes_by_file.setdefault(key, []).append(ch)
            commit.changes = [
                c
                for group in changes_by_file.values()
                for c in MergeChangesTransformer.transform(group, commit, project, compute_annotated_lines, change_factory)
            ]
        else:
            commit.changes = [
                c
                for ch in changes
                if (c := ChangeTransformer.transform(ch, commit, project, compute_annotated_lines, change_factory))
                is not None
            ]
        for c in commit.changes:
            c.file.changes.append(c)
        LOG.debug("Transforming changes")

    @staticmethod
    def _get_author(commit_dto: CommitDTO, project: GitProject) -> GitAccount:
        return CommitTransformer._get_account(GitAccountId(email = commit_dto.author_email, name = commit_dto.author_name), project)

    @staticmethod
    def _get_committer(commit_dto: CommitDTO, project: GitProject) -> GitAccount:
        return CommitTransformer._get_account(GitAccountId(email = commit_dto.committer_email, name = commit_dto.committer_name), project)

    @staticmethod
    def _get_account(git_account_id: GitAccountId, project: GitProject) -> GitAccount:
        account = project.account_registry.get_by_id(str(git_account_id))
        if account is None:
            account = GitAccount(git_id = git_account_id, project = project, name= git_account_id.name)
            project.account_registry.add(account)
        return account

    @staticmethod
    def _get_parents_from_ids(parent_ids: List[str], project: GitProject) -> List[GitCommit]:
        return [p for pid in parent_ids if (p := project.git_commit_registry.get_by_id(pid)) is not None]

class GitProjectTransformer:
    _branch_id: int = 0

    def __init__(
        self,
        git_log_dto: GitLogDTO,
        name: str = "Project",
        compute_annotated_lines: bool = False,
        change_factory: Optional[ChangeFactory] = None,
    ):
        self.git_log_dto = git_log_dto
        self.name = name
        self.compute_annotated_lines = compute_annotated_lines
        self.change_factory = change_factory or SimpleChangeFactory()

    def transform(self) -> GitProject:
        project = GitProject(name = self.name)
        LOG.info("Creating GIT project %s", self.name)
        commit_no = len(self.git_log_dto.commits)
        for index, commit_dto in enumerate(self.git_log_dto.commits):
            LOG.info(
                "Creating commit %s / %s (%s%%)\r",
                index + 1,
                commit_no,
                (index + 1) * 100 // commit_no,
            )
            CommitTransformer.add_to_project(
                commit_dto, project, self.compute_annotated_lines, self.change_factory
            )

        first_commit = next(iter(project.git_commit_registry.all), None)
        if first_commit:
            self._compute_branch_ids(first_commit)

        LOG.info("Done creating GIT project %s", self.name)
        return project

    def _compute_branch_ids(self, commit: GitCommit) -> None:
        parents = commit.parents
        if commit.is_merge_commit:
            commit.branch_id = parents[0].branch_id if parents else 0
        elif not parents or parents[0].is_split_commit:
            GitProjectTransformer._branch_id += 1
            commit.branch_id = GitProjectTransformer._branch_id
        else:
            commit.branch_id = parents[0].branch_id



