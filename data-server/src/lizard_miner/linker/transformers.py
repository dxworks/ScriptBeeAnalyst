"""Transform Lizard CSV rows into FileMetric domain objects.

Implements §4 of communication/B1_lizard/index_step_general.md.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Iterable, List, Optional

from src.common.lizard_models import FileMetric, FunctionMetric
from src.lizard_miner.reader_dto.models import LizardRowDTO
from src.logger import get_logger

LOG = get_logger(__name__)


class LizardProjectTransformer:
    """Group LizardRowDTOs by file and emit per-file FileMetric rollups.

    `repo_root` (optional) is used to convert Lizard's absolute paths to
    repo-relative ones — Lizard runs against an absolute path on the runner's
    host, so we strip everything up to and including the repo root so the
    file_path joins with git File.relative_path() (and the iglog repo prefix).
    """

    def __init__(
        self,
        rows: Iterable[LizardRowDTO],
        repo_root: Optional[str] = None,
        repo_prefix: Optional[str] = None,
        source: str = "lizard",
    ):
        self.rows = list(rows)
        self.repo_root = repo_root.rstrip(os.sep) if repo_root else None
        self.repo_prefix = repo_prefix
        self.source = source

    def transform(self) -> List[FileMetric]:
        by_file: dict[str, list[LizardRowDTO]] = defaultdict(list)
        for row in self.rows:
            normalised = _normalise_path(row.file, self.repo_root, self.repo_prefix)
            by_file[normalised].append(row)

        metrics: List[FileMetric] = []
        for file_path, file_rows in by_file.items():
            functions = [_to_function_metric(r) for r in file_rows]
            ccns = [f.cyclomatic_complexity for f in functions]
            nlocs = [f.nloc for f in functions]
            metrics.append(FileMetric(
                file_path=file_path,
                source=self.source,
                sum_nloc=sum(nlocs),
                max_ccn=max(ccns) if ccns else 0,
                avg_ccn=round(sum(ccns) / len(ccns), 3) if ccns else 0.0,
                function_count=len(functions),
                longest_function_nloc=max(nlocs) if nlocs else 0,
                functions=functions,
            ))
        LOG.info(
            "Built %d FileMetric rollups from %d Lizard rows",
            len(metrics), len(self.rows),
        )
        return metrics


def _to_function_metric(row: LizardRowDTO) -> FunctionMetric:
    return FunctionMetric(
        name=row.function,
        long_name=row.long_name,
        class_name=row.class_name,
        nloc=row.nloc,
        cyclomatic_complexity=row.ccn,
        parameters=row.param,
        token_count=row.token,
        length=row.length,
        start_line=row.start,
        end_line=row.end,
    )


def _normalise_path(
    raw_path: str,
    repo_root: Optional[str],
    repo_prefix: Optional[str],
) -> str:
    path = raw_path.replace("\\", "/").lstrip("./")
    if not path.startswith("/") and not (len(path) > 1 and path[1] == ":"):
        path = "/" + path if raw_path.startswith("/") else path
    # If we know the repo root, strip the prefix up to and including it so the
    # remainder joins with the iglog-side repo prefix added in processor.py.
    if repo_root:
        root = repo_root.replace("\\", "/").rstrip("/")
        idx = path.rfind(root + "/")
        if idx >= 0:
            path = path[idx + len(root) + 1:]
    path = path.lstrip("./").lstrip("/")
    if repo_prefix:
        prefix = repo_prefix.strip("/")
        if prefix and not path.startswith(prefix + "/"):
            path = f"{prefix}/{path}"
    return path
