import logging
from typing import List, Dict

from src.inspector_git.reader.dto.gitlog.git_log_dto import GitLogDTO
from src.inspector_git.reader.git_client import GitClient
from src.inspector_git.reader.iglog.iglog_constants import IGLogConstants
from src.inspector_git.reader.parsers.commit_parser_factory import CommitParserFactory

class LogParser:
    """
    Python equivalent of Kotlin LogParser.
    Parses git log lines into GitLogDTO objects using CommitParserFactory.
    """

    LOG = logging.getLogger("LogParser")

    def __init__(self, git_client: GitClient):
        self.git_client = git_client

    @staticmethod
    def extract_commits(lines: List[str]) -> List[List[str]]:
        """
        Extracts commits from lines of git log output.
        Each commit starts with IGLogConstants.commit_id_prefix.
        """
        commits: List[List[str]] = []
        current_commit_lines: List[str] = []
        LogParser.LOG.debug("Extracting commits")
        for line in lines:
            if line.startswith(IGLogConstants.commit_id_prefix):
                current_commit_lines = []
                commits.append(current_commit_lines)
            current_commit_lines.append(line)
        return commits

    def parse(self, lines: List[str]) -> GitLogDTO:
        """
        Parses a list of git log lines into a GitLogDTO.
        """
        commits = self.extract_commits(lines)
        self.LOG.debug(f"Found {len(commits)} commits")
        # Group by commit id
        id_to_commit_map: Dict[str, List[List[str]]] = {}
        for commit_lines in commits:
            commit_id = self.get_commit_id(commit_lines)
            id_to_commit_map.setdefault(commit_id, []).append(commit_lines)
        # Convert grouped commits into CommitDTO objects
        commit_dtos = [
            CommitParserFactory.create_and_parse(commit_group, self.git_client)
            for commit_group in id_to_commit_map.values()
        ]
        return GitLogDTO(commits=commit_dtos)

    @staticmethod
    def get_commit_id(commit_lines: List[str]) -> str:
        """
        Extracts the commit ID from a commit's lines.
        """
        return commit_lines[0].removeprefix(IGLogConstants.commit_id_prefix)
