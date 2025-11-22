# /src/inspector_git/dto/gitlog/commit_dto.py
from typing import List

from src.inspector_git.reader.dto.commit_info_dto import CommitInfoDTO
from src.inspector_git.reader.dto.gitlog.chnage_dto import ChangeDTO


class CommitDTO(CommitInfoDTO):
    """
    DTO care reprezintă un commit complet, incluzând lista de schimbări asociate.
    Extinde CommitInfoDTO cu informații suplimentare despre schimbări.
    """

    def __init__(
        self,
        id: str,
        parent_ids: List[str],
        author_name: str,
        author_email: str,
        author_date: str,
        committer_name: str,
        committer_email: str,
        committer_date: str,
        message: str,
        changes: List[ChangeDTO],
    ):
        super().__init__(
            id=id,
            parent_ids=parent_ids,
            author_name=author_name,
            author_email=author_email,
            author_date=author_date,
            committer_name=committer_name,
            committer_email=committer_email,
            committer_date=committer_date,
            message=message,
        )
        self.changes: List[ChangeDTO] = changes
