from dataclasses import dataclass

from src.inspector_git.reader.dto.iglog.hunk_change_meta import HunkChangeMeta
from src.inspector_git.reader.dto.iglog.line_operations_meta import LineOperationsMeta


@dataclass
class IgHunkDTO:
    line_operations_meta: LineOperationsMeta
    hunk_change_meta: HunkChangeMeta

