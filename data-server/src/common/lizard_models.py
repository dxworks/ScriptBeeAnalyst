"""Per-file complexity metrics ingested from Lizard CSV (and Metrix++ stub).

Implements §3 of communication/B1_lizard/index_step_general.md.

A `FileMetric` is the file-level rollup (sum_nloc, max_ccn, avg_ccn,
function_count) computed in the transformer; `FunctionMetric` is the per-row
record straight from Lizard. Both are picklable plain Pydantic models — they
do not back-reference the GitProject and do not need __reduce__ shims.
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.common.registries import AbstractRegistry


class FunctionMetric(BaseModel):
    """One function's complexity metrics as Lizard reports it."""
    model_config = ConfigDict(frozen=False)

    name: str
    long_name: str
    class_name: Optional[str] = None
    nloc: int
    cyclomatic_complexity: int
    parameters: int
    token_count: int
    length: int
    start_line: int
    end_line: int


class FileMetric(BaseModel):
    """File-level rollup over its FunctionMetric rows."""
    model_config = ConfigDict(frozen=False)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    file_path: str
    source: str
    sum_nloc: int = 0
    max_ccn: int = 0
    avg_ccn: float = 0.0
    function_count: int = 0
    longest_function_nloc: int = 0
    functions: List[FunctionMetric] = Field(default_factory=list)


class FileMetricRegistry(AbstractRegistry[FileMetric, str]):
    """Keyed by file_path so taggers can look up a FileMetric for a given file id."""

    def get_id(self, entity: FileMetric) -> str:
        return entity.file_path
