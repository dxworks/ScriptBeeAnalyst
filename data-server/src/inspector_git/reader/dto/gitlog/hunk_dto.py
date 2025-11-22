from src.inspector_git.reader.dto.gitlog.hunk_type import HunkType
from src.inspector_git.reader.dto.gitlog.line_chnage_dto import LineChangeDTO
from src.inspector_git.reader.enums.line_operation import LineOperation


class HunkDTO:
    def __init__(self, line_changes: list[LineChangeDTO]):
        self._line_changes: list[LineChangeDTO] = line_changes
        self._update_added_deleted(line_changes)

    @property
    def line_changes(self) -> list[LineChangeDTO]:
        return self._line_changes

    @line_changes.setter
    def line_changes(self, value: list[LineChangeDTO]):
        self._line_changes = value
        self._update_added_deleted(value)

    @property
    def added_line_changes(self) -> list[LineChangeDTO]:
        return self._added_line_changes

    @property
    def deleted_line_changes(self) -> list[LineChangeDTO]:
        return self._deleted_line_changes

    @property
    def type(self) -> HunkType:
        if not self.added_line_changes:
            return HunkType.DELETE
        elif not self.deleted_line_changes:
            return HunkType.ADD
        else:
            return HunkType.MODIFY

    def _update_added_deleted(self, changes: list[LineChangeDTO]):
        added, deleted = [], []
        for change in changes:
            if change.operation == LineOperation.ADD:
                added.append(change)
            else:
                deleted.append(change)
        self._added_line_changes = added
        self._deleted_line_changes = deleted
