# /src/inspector_git/gitclient/parsers/impl/hunk_parser.py

from typing import List

from src.inspector_git.reader.parsers.git_parser import GitParser
from src.inspector_git.reader.dto.gitlog.hunk_dto import HunkDTO
from src.inspector_git.reader.dto.gitlog.line_chnage_dto import LineChangeDTO
from src.inspector_git.reader.enums.line_operation import LineOperation


class HunkParser(GitParser[HunkDTO]):
    def parse(self, lines: List[str]) -> HunkDTO:
        line_changes = self._extract_line_changes(lines)
        return HunkDTO(line_changes)

    def _extract_line_changes(self, lines: List[str]) -> List[LineChangeDTO]:
        from_line_number, to_line_number = self._get_from_and_to_line_numbers(lines[0])

        deleted_line_index = 0
        added_line_index = 0
        line_changes: List[LineChangeDTO] = []

        for line in lines[1:]:
            if line.startswith("-"):
                line_changes.append(
                    LineChangeDTO(
                        operation=LineOperation.DELETE,
                        number=from_line_number + deleted_line_index,
                        content=line[1:],
                    )
                )
                deleted_line_index += 1
            elif line.startswith("+"):
                line_changes.append(
                    LineChangeDTO(
                        operation=LineOperation.ADD,
                        number=to_line_number + added_line_index,
                        content=line[1:],
                    )
                )
                added_line_index += 1
            else:
                deleted_line_index += 1
                added_line_index += 1

        return line_changes

    def _get_from_and_to_line_numbers(self, hunk_info_line: str) -> tuple[int, int]:
        numbers = hunk_info_line.split("@ ")[1].split(" @")[0]
        delete_and_add_info = numbers.split(" ")
        return (
            self._get_start_line_number(delete_and_add_info[0]),
            self._get_start_line_number(delete_and_add_info[1]),
        )

    def _get_start_line_number(self, info: str) -> int:
        numbers = info[1:]
        line_number_and_count = numbers.split(",")
        return int(line_number_and_count[0])
