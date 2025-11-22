from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class LineOperationsMeta:
    add_ranges: List[Tuple[int, int]]
    delete_ranges: List[Tuple[int, int]]
