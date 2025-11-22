from typing import List

from src.inspector_git.reader.dto.gitlog.chnage_dto import ChangeDTO
from src.inspector_git.reader.parsers.abstracts.commit_parser import CommitParser
from src.inspector_git.reader.parsers.impl.change_parser import ChangeParser


class SimpleCommitParser(CommitParser):
    def get_changes(self, lines: List[str], commit_id: str, parent_ids: List[str]) -> List[ChangeDTO]:
        if lines:
            return [
                ChangeParser(parent_ids[0] if parent_ids else "").parse(change_lines)
                for change_lines in self.extract_changes(lines)
            ]
        return []
