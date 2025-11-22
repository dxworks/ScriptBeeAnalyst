from dataclasses import dataclass
from typing import List

from .ig_commit_dto import IgCommitDTO  # assumes IgCommitDTO exists in same package


@dataclass
class IgLogDTO:
    """
    DTO pentru log-ul IG: conține o listă de commit-uri detaliate (IgCommitDTO).
    Echivalentul Kotlin: class IgLogDTO(val commits: List<IgCommitDTO>)
    """
    commits: List[IgCommitDTO]

    def __post_init__(self):
        # normalizează pentru a evita None și garantează o listă
        if self.commits is None:
            self.commits = []
