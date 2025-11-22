from io import StringIO

from src.inspector_git.reader.dto.gitlog.commit_dto import CommitDTO
from src.inspector_git.reader.iglog.iglog_constants import IGLogConstants
from src.inspector_git.reader.iglog.writers.ig_change_writer import IGChangeWriter
from src.inspector_git.reader.iglog.writers.ig_writer import IGWriter
from src.inspector_git.reader.incognito.char_transformer import encrypt_string



class IGCommitWriter(IGWriter):
    def __init__(self, commit_dto: CommitDTO, incognito: bool):
        super().__init__(incognito)
        self.commit_dto = commit_dto

    def append_lines(self, response_builder: StringIO) -> None:
        response_builder.write(self._get_id_line() + "\n")
        response_builder.write(self._get_parents_line() + "\n")
        response_builder.write(self.commit_dto.author_date + "\n")
        response_builder.write(
            (encrypt_string(self.commit_dto.author_email) if self._incognito else self.commit_dto.author_email) + "\n"
        )
        response_builder.write(
            (encrypt_string(self.commit_dto.author_name) if self._incognito else self.commit_dto.author_name) + "\n"
        )

        if self.commit_dto.author_date != self.commit_dto.committer_date:
            response_builder.write(self.commit_dto.committer_date + "\n")
            response_builder.write(
                (encrypt_string(self.commit_dto.committer_email) if self._incognito else self.commit_dto.committer_email) + "\n"
            )
            response_builder.write(
                (encrypt_string(self.commit_dto.committer_name) if self._incognito else self.commit_dto.committer_name) + "\n"
            )

        response_builder.write(self._get_message_line() + "\n")

        for change in self.commit_dto.changes:
            response_builder.write(IGChangeWriter(change).write())

    def _get_message_line(self) -> str:
        return f"{IGLogConstants.message_prefix}{self._get_formatted_message()}"

    def _get_formatted_message(self) -> str:
        return self.commit_dto.message.replace("\n", f"\n{IGLogConstants.message_prefix}")

    def _get_id_line(self) -> str:
        return f"{IGLogConstants.commit_id_prefix}{self.commit_dto.id}"

    def _get_parents_line(self) -> str:
        return " ".join(self.commit_dto.parent_ids)
