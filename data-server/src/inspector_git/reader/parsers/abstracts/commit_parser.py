from abc import ABC, abstractmethod
from typing import List
import logging

from src.inspector_git.reader.dto.gitlog.chnage_dto import ChangeDTO
from src.inspector_git.reader.dto.gitlog.commit_dto import CommitDTO
from src.inspector_git.reader.iglog.iglog_constants import IGLogConstants
from src.inspector_git.reader.parsers.git_parser import GitParser


logger = logging.getLogger(__name__)


class CommitParser(GitParser[CommitDTO], ABC):
    def parse(self, lines: List[str]) -> CommitDTO:
        mutable_lines = list(lines)
        commit_id = self._extract_commit_id(mutable_lines)
        logger.debug(f"Parsing commit with id: {commit_id}")
        parent_ids = self._extract_parent_ids(mutable_lines)
        return CommitDTO(
            id=commit_id,
            parent_ids=parent_ids,
            author_name=mutable_lines.pop(0).strip(),
            author_email=mutable_lines.pop(0).strip(),
            author_date=mutable_lines.pop(0).strip(),
            committer_name=mutable_lines.pop(0).strip(),
            committer_email=mutable_lines.pop(0).strip(),
            committer_date=mutable_lines.pop(0).strip(),
            message=self._extract_message(mutable_lines),
            changes=self.get_changes(mutable_lines, commit_id, parent_ids),
        )

    @abstractmethod
    def get_changes(self, lines: List[str], commit_id: str, parent_ids: List[str]) -> List[ChangeDTO]:
        pass

    def extract_changes(self, lines: List[str]) -> List[List[str]]:
        changes: List[List[str]] = []
        current_change_lines: List[str] = []
        logger.debug("Extracting changes")
        started = False
        for line in lines:
            if not started and not line.startswith(IGLogConstants.git_log_diff_line_start):
                continue
            if line.startswith(IGLogConstants.git_log_diff_line_start):
                current_change_lines = []
                changes.append(current_change_lines)
                started = True
            current_change_lines.append(line)
        logger.debug(f"Found {len(changes)} changes")
        return changes

    def _extract_commit_id(self, lines: List[str]) -> str:
        return lines.pop(0).removeprefix(IGLogConstants.commit_id_prefix)

    def _extract_parent_ids(self, lines: List[str]) -> List[str]:
        return [pid for pid in lines.pop(0).split(" ") if pid]

    def _extract_message(self, lines: List[str]) -> str:
        message_lines: List[str] = []
        while lines and lines[0] != IGLogConstants.git_log_message_end:
            message_lines.append(lines.pop(0))
        if lines:
            lines.pop(0)  # remove the gitLogMessageEnd line
        return "\n".join(message_lines).strip()
