from typing import List, Tuple

from src.inspector_git.reader.dto.gitlog.chnage_dto import ChangeDTO
from src.inspector_git.reader.dto.gitlog.hunk_dto import HunkDTO
from src.inspector_git.reader.enums.chnage_type import ChangeType
from src.inspector_git.reader.iglog.iglog_constants import IGLogConstants
from src.inspector_git.reader.iglog.readers.ig_hunk_reader import IgHunkReader
from src.inspector_git.utils.constants import DEV_NULL

class IGChangeReader:
    def __init__(self, ig_hunk_reader: IgHunkReader | None = None):
        self.ig_hunk_reader = ig_hunk_reader or IgHunkReader()

    def read(self, lines: List[str]) -> ChangeDTO:
        change_line = lines.pop(0).removeprefix(IGLogConstants.change_prefix)
        change_type, is_binary = self._get_type(change_line)

        parent_commit_id = lines.pop(0)
        old_file_name, new_file_name = self._get_file_name(lines, change_type)

        hunks: List[HunkDTO] = []
        if not is_binary:
            current_hunk_lines: List[str] = []
            for line in lines:
                if line.startswith(IGLogConstants.hunk_prefix_line):
                    if current_hunk_lines:
                        hunks.append(self.ig_hunk_reader.read(current_hunk_lines))
                    current_hunk_lines = []
                current_hunk_lines.append(line)
            if current_hunk_lines:
                hunks.append(self.ig_hunk_reader.read(current_hunk_lines))

        return ChangeDTO(
            old_file_name.strip(),
            new_file_name.strip(),
            change_type,
            parent_commit_id,
            is_binary,
            hunks,
        )

    def _get_file_name(self, lines: List[str], change_type: ChangeType) -> Tuple[str, str]:
        file_name = lines.pop(0)
        if change_type == ChangeType.ADD:
            return DEV_NULL, file_name
        elif change_type == ChangeType.DELETE:
            return file_name, DEV_NULL
        elif change_type == ChangeType.RENAME:
            return file_name, lines.pop(0)
        elif change_type == ChangeType.MODIFY:
            return file_name, file_name
        else:
            raise ValueError(f"Unknown change type: {change_type}")

    def _get_type(self, line: str) -> Tuple[ChangeType, bool]:
        first_char = line[0]
        if first_char == "A":
            change_type = ChangeType.ADD
        elif first_char == "D":
            change_type = ChangeType.DELETE
        elif first_char == "R":
            change_type = ChangeType.RENAME
        else:
            change_type = ChangeType.MODIFY
        is_binary = len(line) > 1
        return change_type, is_binary
