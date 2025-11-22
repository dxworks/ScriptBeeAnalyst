from typing import List

from src.inspector_git.reader.dto.gitlog.chnage_dto import ChangeDTO
from src.inspector_git.reader.dto.gitlog.commit_dto import CommitDTO
from src.inspector_git.reader.iglog.iglog_constants import IGLogConstants
from src.inspector_git.reader.iglog.readers.ig_change_reader import IGChangeReader

class IGCommitReader:
    def __init__(self, ig_change_reader: IGChangeReader | None = None):
        self.ig_change_reader = ig_change_reader or IGChangeReader()

    def read(self, lines: List[str]) -> CommitDTO:
        commit_id = lines.pop(0).removeprefix(IGLogConstants.commit_id_prefix)
        parent_ids = lines.pop(0).split(" ")
        author_date = lines.pop(0)
        author_email = lines.pop(0)
        author_name = lines.pop(0)

        committer_date = ""
        committer_email = ""
        committer_name = ""

        if lines[0].startswith(IGLogConstants.message_prefix):
            message = self._extract_message(lines)
        else:
            committer_date = lines.pop(0)
            committer_email = lines.pop(0)
            committer_name = lines.pop(0)
            message = self._extract_message(lines)

        current_change_lines: List[str] = []
        changes: List[ChangeDTO] = []

        for line in lines:
            if line.startswith(IGLogConstants.change_prefix):
                if current_change_lines:
                    changes.append(self.ig_change_reader.read(current_change_lines))
                current_change_lines = []
            current_change_lines.append(line)

        if current_change_lines:
            changes.append(self.ig_change_reader.read(current_change_lines))

        return CommitDTO(
            id=commit_id,
            parent_ids=parent_ids,
            author_name=author_name,
            author_email=author_email,
            author_date=author_date,
            committer_name=committer_name,
            committer_email=committer_email,
            committer_date=committer_date,
            message=message,
            changes=changes,
        )

    def _extract_message(self, commit_lines: List[str]) -> str:
        message_lines: List[str] = []
        while commit_lines and commit_lines[0].startswith(IGLogConstants.message_prefix):
            message_lines.append(commit_lines.pop(0).removeprefix(IGLogConstants.message_prefix))
        return "\n".join(message_lines).strip()
