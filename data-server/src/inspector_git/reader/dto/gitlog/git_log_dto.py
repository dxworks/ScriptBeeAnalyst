# /src/inspector_git/dto/gitlog/git_log_dto.py

from dataclasses import dataclass
from typing import List
from .commit_dto import CommitDTO


@dataclass
class GitLogDTO:
    """
    DTO ce reprezintă un git log – conține lista de commituri.
    """
    commits: List[CommitDTO]
