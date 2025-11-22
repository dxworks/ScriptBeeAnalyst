from typing import List, Tuple

import logging

from src.inspector_git.reader.dto.gitlog.chnage_dto import ChangeDTO
from src.inspector_git.reader.enums.chnage_type import ChangeType
from src.inspector_git.reader.parsers.git_parser import GitParser
from src.inspector_git.reader.parsers.impl.hunk_parser import HunkParser
from src.inspector_git.utils.constants import DEV_NULL

LOG = logging.getLogger(__name__)


class ChangeParser(GitParser[ChangeDTO]):
    def __init__(self, parent_commit_id: str):
        self.parent_commit_id = parent_commit_id

    def parse(self, lines: List[str]) -> ChangeDTO:
        change_type = self._extract_change_type(lines)
        old_file_name, new_file_name = self._extract_file_names(lines, change_type)

        LOG.debug(f"Parsing {change_type} change for {old_file_name} -> {new_file_name}")

        hunks = [HunkParser().parse(hunk) for hunk in self._extract_hunks(lines)]

        return ChangeDTO(
            type=change_type,
            old_file_name=old_file_name.strip(),
            new_file_name=new_file_name.strip(),
            parent_commit_id=self.parent_commit_id,
            hunks=hunks,
            is_binary=any(line.startswith("Binary files") for line in lines),
        )

    def _extract_hunks(self, lines: List[str]) -> List[List[str]]:
        hunks: List[List[str]] = []
        current_hunk_lines: List[str] = []

        LOG.debug("Extracting hunks")
        first_hunk_index = next((i for i, line in enumerate(lines) if line.startswith("@")), -1)

        if first_hunk_index == -1:
            return []

        for line in lines[first_hunk_index:]:
            if line.startswith("@"):
                current_hunk_lines = []
                hunks.append(current_hunk_lines)

            if line == "\\ No newline at end of file":
                if current_hunk_lines:
                    last_line = current_hunk_lines.pop()
                    current_hunk_lines.append(last_line[:-1])
            else:
                current_hunk_lines.append(line + "\n")

        LOG.debug(f"Found {len(hunks)} hunks")
        return hunks

    def _extract_change_type(self, lines: List[str]) -> ChangeType:
        if any(line.startswith("new file mode") for line in lines):
            return ChangeType.ADD
        elif any(line.startswith("deleted file mode") for line in lines):
            return ChangeType.DELETE
        elif any(line.startswith("similarity index") for line in lines):
            return ChangeType.RENAME
        return ChangeType.MODIFY

    def _extract_file_names(self, lines: List[str], change_type: ChangeType) -> Tuple[str, str]:
        old_file_prefix = "rename from " if change_type == ChangeType.RENAME else "--- a/"
        new_file_prefix = "rename to " if change_type == ChangeType.RENAME else "+++ b/"

        old_file_name = DEV_NULL if change_type == ChangeType.ADD else self._extract_file_name(lines, old_file_prefix)
        new_file_name = DEV_NULL if change_type == ChangeType.DELETE else self._extract_file_name(lines, new_file_prefix)

        return old_file_name, new_file_name

    def _extract_file_name(self, lines: List[str], file_name_prefix: str) -> str:
        name_line = next((line for line in lines if line.startswith(file_name_prefix)), None)
        if name_line:
            return name_line[len(file_name_prefix) :]
        return self.extract_file_name(lines[0])

    def extract_file_name(self, diff_line: str) -> str:
        names_start_index = diff_line.find(" a/") + 3
        names = diff_line[names_start_index:]
        names_parts = names.split(" b/")
        return " b/".join(names_parts[: len(names_parts) // 2])
