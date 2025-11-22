from src.inspector_git.reader.dto.gitlog.git_log_dto import GitLogDTO
from src.inspector_git.reader.iglog.writers.ig_commit_writer import IGCommitWriter
from src.inspector_git.reader.iglog.writers.ig_writer import IGWriter


class IGLogWriter(IGWriter):
    def __init__(self, git_log_dto: GitLogDTO, incognito: bool = False):
        super().__init__(incognito)
        self.git_log_dto = git_log_dto

    def append_lines(self, response_builder):
        for commit in self.git_log_dto.commits:
            commit_writer = IGCommitWriter(commit, self._incognito)
            response_builder.write(commit_writer.write())
