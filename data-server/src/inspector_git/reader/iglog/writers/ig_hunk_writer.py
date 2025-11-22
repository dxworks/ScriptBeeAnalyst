from io import StringIO
from src.inspector_git.reader.dto.gitlog.hunk_dto import HunkDTO
from src.inspector_git.reader.iglog.iglog_constants import IGLogConstants
from src.inspector_git.reader.iglog.writers.ig_writer import IGWriter


class IGHunkWriter(IGWriter):
    def __init__(self, hunk_dto: HunkDTO, incognito: bool = False):
        super().__init__(incognito)
        self.hunk_dto = hunk_dto

    def append_lines(self, response_builder: StringIO):
        response_builder.write(IGLogConstants.hunk_prefix_line)
        for line_change in self.hunk_dto.line_changes:
            response_builder.write(f"{line_change.content}\n")
