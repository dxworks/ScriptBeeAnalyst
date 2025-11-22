from dataclasses import dataclass, field
from typing import List


@dataclass
class CommitInfoDTO:
    """
    DTO care reprezintă informațiile despre un commit Git.
    """
    id: str
    parent_ids: List[str]
    author_name: str
    author_email: str
    author_date: str
    message: str
    committer_name: str = field(repr=False)
    committer_email: str = field(repr=False)
    committer_date: str = field(repr=False)
