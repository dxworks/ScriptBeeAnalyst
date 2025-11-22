from src.inspector_git.reader.dto.gitlog.chnage_dto import ChangeDTO
from src.inspector_git.reader.enums.chnage_type import ChangeType
from src.inspector_git.reader.iglog.iglog_constants import IGLogConstants
from src.inspector_git.reader.iglog.writers.ig_hunk_writer import IGHunkWriter
from src.inspector_git.reader.iglog.writers.ig_writer import IGWriter



class IGChangeWriter(IGWriter):
    def __init__(self, change_dto: ChangeDTO):
        super().__init__()
        self.change_dto = change_dto

    def append_lines(self, response_builder: list[str]) -> None:
        response_builder.append(self._get_type_line())
        response_builder.append(self.change_dto.parent_commit_id)
        response_builder.append(self._get_file_names())

        for hunk in self.change_dto.hunks:
            response_builder.append(IGHunkWriter(hunk).write())

    def _get_file_names(self) -> str:
        change_type = self.change_dto.type
        if change_type == ChangeType.ADD:
            return self.change_dto.new_file_name
        elif change_type == ChangeType.DELETE:
            return self.change_dto.old_file_name
        elif change_type == ChangeType.RENAME:
            return f"{self.change_dto.old_file_name}\n{self.change_dto.new_file_name}"
        elif change_type == ChangeType.MODIFY:
            return self.change_dto.new_file_name
        return ""

    def _get_type_line(self) -> str:
        return f"{IGLogConstants.change_prefix}{self._get_type_letter()}{self._is_binary()}"

    def _is_binary(self) -> str:
        return "b" if self.change_dto.is_binary else ""

    def _get_type_letter(self) -> str:
        change_type = self.change_dto.type
        if change_type == ChangeType.ADD:
            return "A"
        elif change_type == ChangeType.DELETE:
            return "D"
        elif change_type == ChangeType.RENAME:
            return "R"
        elif change_type == ChangeType.MODIFY:
            return "M"
        return "?"
