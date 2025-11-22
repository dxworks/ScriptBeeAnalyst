from typing import List

from src.inspector_git.reader.dto.gitlog.commit_dto import CommitDTO
from src.inspector_git.reader.git_client import GitClient
from src.inspector_git.reader.parsers.impl.merge_commit_parser import MergeCommitParser
from src.inspector_git.reader.parsers.impl.simple_commit_parser import SimpleCommitParser


class CommitParserFactory:
    @staticmethod
    def create_and_parse(commits_group: List[List[str]], git_client: GitClient) -> CommitDTO:
        """
        Creează parser-ul potrivit în funcție de numărul de părinți ai commitului
        și returnează CommitDTO-ul rezultat din parsare.
        """
        if CommitParserFactory.get_number_of_parents(commits_group[0]) > 1:
            return MergeCommitParser(commits_group, git_client).parse([])
        else:
            return SimpleCommitParser().parse(commits_group[0])

    @staticmethod
    def get_number_of_parents(lines: List[str]) -> int:
        """
        Returnează numărul de părinți ai commitului pe baza celei de-a doua linii.
        """
        return len(lines[1].split(" "))
