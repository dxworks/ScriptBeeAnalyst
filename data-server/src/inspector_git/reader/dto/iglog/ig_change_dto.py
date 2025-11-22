from src.inspector_git.reader.dto.change_info_dto import ChangeInfoDTO
from src.inspector_git.reader.dto.iglog.ig_hunk_dto import IgHunkDTO
from src.inspector_git.reader.enums.chnage_type import ChangeType


class IgChangeDTO(ChangeInfoDTO):
    def __init__(
        self,
        old_file_name: str,
        new_file_name: str,
        type: ChangeType,
        parent_commit_id: str,
        is_binary: bool,
        ig_hunk_dto: IgHunkDTO,
    ):
        super().__init__(old_file_name, new_file_name, type, parent_commit_id, is_binary)
        self.ig_hunk_dto: IgHunkDTO = ig_hunk_dto
