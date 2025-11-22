from src.inspector_git.reader.dto.gitlog.hunk_dto import HunkDTO
from src.inspector_git.reader.dto.gitlog.line_chnage_dto import LineChangeDTO
from src.inspector_git.reader.enums.line_operation import LineOperation
from src.inspector_git.reader.extractors.impl.line_operations_meta_extractor import LineOperationsMetaExtractor
from src.inspector_git.reader.iglog.iglog_constants import IGLogConstants


class IgHunkReader:
    def read(self, lines: list[str]) -> HunkDTO:
        meta = LineOperationsMetaExtractor().read(
            lines[0].removeprefix(IGLogConstants.hunk_prefix_line)
        )

        line_changes = (
            [lc for rng in meta.add_ranges for lc in self._get_line_change_dtos(rng, LineOperation.ADD)]
            + [lc for rng in meta.delete_ranges for lc in self._get_line_change_dtos(rng, LineOperation.DELETE)]
        )

        return HunkDTO(line_changes)

    def _get_line_change_dtos(self, rng: tuple[int, int], operation: LineOperation) -> list[LineChangeDTO]:
        start, end = rng
        return [LineChangeDTO(operation, number, None) for number in range(start, end + 1)]
