from typing import List
from src.inspector_git.reader.dto.change_info_dto import ChangeInfoDTO
from src.inspector_git.reader.dto.gitlog.hunk_dto import HunkDTO
from src.inspector_git.reader.enums.chnage_type import ChangeType


class ChangeDTO(ChangeInfoDTO):
    """
    DTO care extinde ChangeInfoDTO și adaugă lista de hunks asociate schimbării.
    """
    def __init__(
        self,
        old_file_name: str,
        new_file_name: str,
        type: ChangeType,
        parent_commit_id: str,
        is_binary: bool,
        hunks: List[HunkDTO]
    ):
        super().__init__(old_file_name, new_file_name, type, parent_commit_id, is_binary)
        self.hunks: List[HunkDTO] = hunks
