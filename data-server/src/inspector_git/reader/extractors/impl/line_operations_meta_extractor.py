from typing import List, Tuple

from src.inspector_git.reader.dto.gitlog.hunk_dto import HunkDTO
from src.inspector_git.reader.dto.gitlog.line_chnage_dto import LineChangeDTO
from src.inspector_git.reader.dto.iglog.line_operations_meta import LineOperationsMeta
from src.inspector_git.reader.extractors.meta_extractor import MetaExtractor

class LineOperationsMetaExtractor(MetaExtractor[LineOperationsMeta]):
    splitter = "|"
    ranges_splitter = " "
    range_marker = ":"
    pair_zero: Tuple[int, int] = (0, 0)

    @property
    def line_prefix(self) -> str:
        return "="

    def extract(self, hunk_dto: HunkDTO) -> str:
        deleted_lines = hunk_dto.deleted_line_changes
        delete_ranges = self._extract_ranges(deleted_lines)

        added_lines = hunk_dto.added_line_changes
        add_ranges = self._extract_ranges(added_lines)

        return f"{self._get_formatted_ranges(add_ranges)}{self.splitter}{self._get_formatted_ranges(delete_ranges)}"

    def _extract_ranges(self, lines: List[LineChangeDTO]) -> List[Tuple[int, int]]:
        if lines:
            if self._all_lines_are_consecutive(lines):
                return [(lines[0].number, lines[-1].number)]
            else:
                return self._extract_number_ranges([l.number for l in lines])
        else:
            return [self.pair_zero]

    def _extract_number_ranges(self, numbers: List[int]) -> List[Tuple[int, int]]:
        range_start = numbers[0]
        range_end = numbers[0]
        ranges: List[Tuple[int, int]] = []

        for i in range(len(numbers)):
            if i == len(numbers) - 1 or numbers[i] + 1 != numbers[i + 1]:
                ranges.append((range_start, range_end))
                if i != len(numbers) - 1:
                    range_start = numbers[i + 1]
                    range_end = numbers[i + 1]
            else:
                range_end += 1
        return ranges

    def _all_lines_are_consecutive(self, lines: List[LineChangeDTO]) -> bool:
        min_val = lines[0].number - 1
        total_sum = sum(l.number - min_val for l in lines)
        n = len(lines)
        return n * (n + 1) // 2 == total_sum

    def _get_formatted_ranges(self, ranges: List[Tuple[int, int]]) -> str:
        return self.ranges_splitter.join(self._get_formatted_range(r) for r in ranges)

    def _get_formatted_range(self, range_: Tuple[int, int]) -> str:
        if range_ == self.pair_zero:
            return "0"
        if range_[0] == range_[1]:
            return str(range_[0])
        return f"{range_[0]}{self.range_marker}{range_[1]}"

    def parse(self, line: str) -> LineOperationsMeta:
        split = line.split(self.splitter)
        added_lines_ranges = split[0]
        deleted_lines_ranges = split[1]
        return LineOperationsMeta(
            self._parse_ranges(added_lines_ranges),
            self._parse_ranges(deleted_lines_ranges)
        )

    def _parse_ranges(self, line_ranges: str) -> List[Tuple[int, int]]:
        ranges = line_ranges.split(self.ranges_splitter)
        result: List[Tuple[int, int]] = []
        for r in ranges:
            parts = r.split(self.range_marker)
            if len(parts) == 1:
                if parts[0] != "0":
                    line_number = int(parts[0])
                    result.append((line_number, line_number))
            else:
                result.append((int(parts[0]), int(parts[1])))
        return result
