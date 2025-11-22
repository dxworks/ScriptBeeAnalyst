from typing import List

from src.inspector_git.reader.dto.gitlog.commit_dto import CommitDTO
from src.inspector_git.reader.git_client import GitClient
from src.inspector_git.reader.parsers.git_parser import GitParser
from src.inspector_git.reader.parsers.impl.simple_commit_parser import SimpleCommitParser

class MergeCommitParser(GitParser[CommitDTO]):
    def __init__(self, commits_group: List[List[str]], git_client: GitClient):
        self.commits_group = commits_group
        self.git_client = git_client

    def parse(self, lines: List[str]) -> CommitDTO:
        commit_dtos: List[CommitDTO] = [SimpleCommitParser().parse(group) for group in self.commits_group]
        target_commit_dto: CommitDTO = commit_dtos[0]

        if len(commit_dtos) < len(target_commit_dto.parent_ids):
            ordered_parent_ids = self._filter_parent_ids(target_commit_dto)
        else:
            ordered_parent_ids = target_commit_dto.parent_ids

        target_commit_dto.changes = [
            change_dto
            for i, commit_dto in enumerate(commit_dtos)
            for change_dto in commit_dto.changes
            if not setattr(change_dto, "parent_commit_id", ordered_parent_ids[i])
        ]

        return target_commit_dto

    def _filter_parent_ids(self, target_commit_dto: CommitDTO) -> List[str]:
        return [
            pid
            for pid in target_commit_dto.parent_ids
            if self.git_client.diff_file_names(pid, target_commit_dto.id)
        ]
