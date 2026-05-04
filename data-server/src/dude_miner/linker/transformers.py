"""Aggregate DuDe DTO rows into the canonical Duplication domain object.

Implements §4-5 of communication/B3_dude/index_step_general.md.

Per orchestrator decision (§B3):
  * pair canonicalisation: sort `(file_a, file_b)` lexicographically before
    aggregation so `(a, b)` and `(b, a)` collapse onto the same edge.
  * `total_block_length` = sum of `block_length` across rows for that pair.
  * `block_count`        = number of rows for that pair.
  * sibling/external classification is NOT performed here — it lives in the
    relation extractors so the parser stays format-faithful and the rule
    (same immediate parent directory) can evolve independently.
  * path_prefix is configurable (mirrors B2's `JaFaxTransformer.path_prefix`):
    when DuDe paths arrive without the project-id prefix the caller passes
    the prefix in and the transformer prepends it so the file ids join with
    iglog `prefix_change_paths(...)` and JaFax/Lizard normalisation.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, List, Optional

from src.common.duplication_models import (
    Duplication,
    DuplicationInternal,
    DuplicationKind,
    DuplicationPair,
)
from src.dude_miner.reader_dto.models import (
    ExternalDuplicationRowDTO,
    InternalDuplicationEntryDTO,
)
from src.logger import get_logger

LOG = get_logger(__name__)


class DudeDuplicationTransformer:
    """Group DuDe DTO rows by canonical pair / file and emit one Duplication."""

    def __init__(
        self,
        external_rows: Iterable[ExternalDuplicationRowDTO],
        internal_entries: Iterable[InternalDuplicationEntryDTO],
        path_prefix: Optional[str] = None,
        source: str = "dude",
    ):
        self.external_rows = list(external_rows)
        self.internal_entries = list(internal_entries)
        self.path_prefix = path_prefix
        self.source = source

    def transform(self) -> Duplication:
        # Aggregate external rows per canonical pair.
        agg: dict[tuple[str, str], list[int]] = defaultdict(list)
        for row in self.external_rows:
            a = _normalise_path(row.file_a, self.path_prefix)
            b = _normalise_path(row.file_b, self.path_prefix)
            if a == b:
                # DuDe internal duplication is in the JSON file, not the CSV;
                # a self-pair would be a malformed row.
                LOG.warning("Skipping DuDe self-pair row: %r", row)
                continue
            key = tuple(sorted([a, b]))
            agg[key].append(row.block_length)

        external_pairs: List[DuplicationPair] = []
        for (a, b), lengths in agg.items():
            external_pairs.append(DuplicationPair(
                file_a_path=a,
                file_b_path=b,
                total_block_length=sum(lengths),
                block_count=len(lengths),
                kind=DuplicationKind.EXTERNAL,
            ))

        # Build internal-by-file map.
        internal_by_file: dict[str, int] = {}
        for entry in self.internal_entries:
            file_path = _normalise_path(entry.file, self.path_prefix)
            # If a file shows up twice (defensive — DuDe emits one row per file),
            # keep the larger value rather than silently overwriting.
            existing = internal_by_file.get(file_path)
            internal_by_file[file_path] = (
                entry.value if existing is None else max(existing, entry.value)
            )

        LOG.info(
            "Built Duplication: %d external pairs (from %d rows) + %d internal files",
            len(external_pairs), len(self.external_rows), len(internal_by_file),
        )
        return Duplication(
            source=self.source,
            external_pairs=external_pairs,
            internal_by_file=internal_by_file,
        )


def _normalise_path(raw_path: str, path_prefix: Optional[str]) -> str:
    """Normalise a DuDe file path into the same convention as iglog/JaFax/Lizard.

    DuDe paths are POSIX, repo-relative-but-ALREADY-prefixed in the observed
    Zeppelin run (e.g. `zeppelin/flink/.../Foo.java`). Per orchestrator
    decision the caller may pass `path_prefix` to prepend the project-id
    segment when the input lacks it; if the path already begins with that
    prefix the function is a no-op so the same code handles both shapes.
    """
    path = raw_path.replace("\\", "/").strip().lstrip("./").lstrip("/")
    if path_prefix:
        prefix = path_prefix.strip("/")
        if prefix and not path.startswith(prefix + "/"):
            path = f"{prefix}/{path}"
    return path
