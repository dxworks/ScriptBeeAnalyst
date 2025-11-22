# /src/inspector_git/gitclient/extractors/metadata_extraction_manager.py

from pathlib import Path
from typing import List, Set

from src.inspector_git.reader.dto.gitlog.git_log_dto import GitLogDTO
from src.inspector_git.reader.dto.gitlog.hunk_dto import HunkDTO
from src.inspector_git.reader.dto.gitlog.line_chnage_dto import LineChangeDTO
from src.inspector_git.reader.enums.line_operation import LineOperation
from src.inspector_git.reader.extractors.impl.line_operations_meta_extractor import LineOperationsMetaExtractor
from src.inspector_git.reader.git_client import GitClient
from src.inspector_git.reader.git_commit_iterator import GitCommitIterator
from src.inspector_git.reader.iglog.writers.ig_log_writer import IGLogWriter
from src.inspector_git.reader.parsers.commit_parser_factory import CommitParserFactory
from src.inspector_git.reader.parsers.log_parser import LogParser



class MetadataExtractionManager:
    _commit_number: int = 1
    _commit_count: int = 0

    def __init__(self, repo_path: Path, extract_to_path: Path, incognito: bool = False):
        self.git_client = GitClient(Path(str(repo_path)))
        self.commit_iterator = GitCommitIterator(self.git_client, 10000)
        self.extract_file = Path(extract_to_path)
        self.incognito = incognito

        self.line_operations_meta_extractor = LineOperationsMetaExtractor()
        self.written_commit_ids: Set[str] = set()
        self.logs_on_hold: List[GitLogDTO] = []

    def extract(self):
        MetadataExtractionManager._commit_number = 1
        MetadataExtractionManager._commit_count = self.git_client.get_commit_count()

        self.extract_file.write_text("Version\n", encoding="utf-8")

        current_commit = next(self.commit_iterator, None)

        while current_commit is not None:
            commit = current_commit
            current_commit = None

            number_of_parents = CommitParserFactory.get_number_of_parents(commit)

            if number_of_parents > 1:
                next_commits = []
                for _ in range(1, number_of_parents):
                    next_commit = next(self.commit_iterator, None)
                    if not next_commit:
                        break
                    if next_commit[0] == commit[0]:
                        next_commits.append(next_commit)
                    else:
                        current_commit = next_commit
                        break
                commits = commit + next_commits
            else:
                commits = commit

            git_log_dto = LogParser(self.git_client).parse(commits)
            self.swap_content_with_metadata(git_log_dto)

            parent_ids = {pid for c in git_log_dto.commits for pid in c.parent_ids}
            if parent_ids.issubset(self.written_commit_ids):
                self.write_git_log(self.extract_file, git_log_dto)
                self.write_logs_on_hold(self.extract_file)
            else:
                self.logs_on_hold.append(git_log_dto)

            if current_commit is None:
                current_commit = next(self.commit_iterator, None)

    def write_logs_on_hold(self, extract_file: Path, i: int = 0):
        if i < len(self.logs_on_hold):
            parent_commit_ids = {pid for c in self.logs_on_hold[i].commits for pid in c.parent_ids}
            if parent_commit_ids.issubset(self.written_commit_ids):
                self.write_git_log(extract_file, self.logs_on_hold[i])
                self.logs_on_hold.pop(i)
                self.write_logs_on_hold(extract_file)
            else:
                self.write_logs_on_hold(extract_file, i + 1)

    def write_git_log(self, extract_file: Path, git_log_dto: GitLogDTO):
        print(
            f"({extract_file.resolve()}) Commit number {MetadataExtractionManager._commit_number} "
            f"of {MetadataExtractionManager._commit_count}. "
            f"({MetadataExtractionManager._commit_number * 100 // MetadataExtractionManager._commit_count}%)\r",
            end=""
        )

        extract_file.write_text("", encoding="utf-8")  # ensure append mode
        extract_file.write_text(IGLogWriter(git_log_dto, self.incognito).write(), encoding="utf-8")

        self.written_commit_ids.update({c.id for c in git_log_dto.commits})
        MetadataExtractionManager._commit_number += 1

    def swap_content_with_metadata(self, git_log_dto: GitLogDTO):
        for commit_dto in git_log_dto.commits:
            for change_dto in commit_dto.changes:
                for hunk_dto in change_dto.hunks:
                    self.swap_content_with_metadata_hunk(hunk_dto)

    def swap_content_with_metadata_hunk(self, hunk_dto: HunkDTO):
        hunk_dto.line_changes = [
            self.ContentOnlyLineChange(self.line_operations_meta_extractor.write(hunk_dto))
        ]

    @staticmethod
    def get_metadata(content: str) -> str:
        return f"{len(content)} {sum(1 for ch in content if ch.isspace())}"

    class ContentOnlyLineChange(LineChangeDTO):
        def __init__(self, content: str):
            super().__init__(LineOperation.ADD, 0, content)
