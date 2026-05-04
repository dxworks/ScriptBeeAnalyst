"""Loaders for DuDe external CSV and internal JSON.

Implements §4 of communication/B3_dude/index_step_general.md.

Both loaders are tolerant: malformed rows are logged and skipped, but
filesystem-level problems (missing file, unreadable JSON) raise so the
processor surfaces them instead of silently producing an empty dataset.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import List

from src.dude_miner.reader_dto.models import (
    ExternalDuplicationRowDTO,
    InternalDuplicationEntryDTO,
)
from src.logger import get_logger

LOG = get_logger(__name__)


class DudeExternalCsvLoader:
    """Reads DuDe's headerless 3-column CSV (file_a, file_b, block_length).

    Uses `csv.reader` (NOT DictReader) because the file has no header — a
    DictReader would silently consume the first data row as field names.
    """

    def __init__(self, csv_path: str):
        self.csv_path = Path(csv_path)

    def load(self) -> List[ExternalDuplicationRowDTO]:
        if not self.csv_path.exists():
            raise FileNotFoundError(f"DuDe external CSV not found: {self.csv_path}")

        rows: List[ExternalDuplicationRowDTO] = []
        with self.csv_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            for line_no, raw in enumerate(reader, start=1):
                if not raw or all(not cell.strip() for cell in raw):
                    continue
                if len(raw) != 3:
                    LOG.warning(
                        "Skipping DuDe external row at %s:%d (expected 3 cols, got %d)",
                        self.csv_path, line_no, len(raw),
                    )
                    continue
                file_a, file_b, block_length_str = raw[0].strip(), raw[1].strip(), raw[2].strip()
                if not file_a or not file_b:
                    LOG.warning(
                        "Skipping DuDe external row at %s:%d (empty file path)",
                        self.csv_path, line_no,
                    )
                    continue
                try:
                    block_length = int(block_length_str)
                except ValueError as e:
                    LOG.warning(
                        "Skipping DuDe external row at %s:%d (block_length=%r not int: %s)",
                        self.csv_path, line_no, block_length_str, e,
                    )
                    continue
                rows.append(ExternalDuplicationRowDTO(
                    file_a=file_a, file_b=file_b, block_length=block_length,
                ))
        LOG.info("Loaded %d DuDe external rows from %s", len(rows), self.csv_path)
        return rows


class DudeInternalJsonLoader:
    """Reads DuDe's internal duplication JSON array."""

    def __init__(self, json_path: str):
        self.json_path = Path(json_path)

    def load(self) -> List[InternalDuplicationEntryDTO]:
        if not self.json_path.exists():
            raise FileNotFoundError(f"DuDe internal JSON not found: {self.json_path}")

        with self.json_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError(
                f"DuDe internal JSON must be a top-level list at {self.json_path}; "
                f"got {type(data).__name__}"
            )

        entries: List[InternalDuplicationEntryDTO] = []
        for idx, raw in enumerate(data):
            if not isinstance(raw, dict):
                LOG.warning(
                    "Skipping DuDe internal entry %d at %s (not an object: %r)",
                    idx, self.json_path, type(raw).__name__,
                )
                continue
            file_path = raw.get("file")
            value = raw.get("value")
            if not isinstance(file_path, str) or not file_path:
                LOG.warning(
                    "Skipping DuDe internal entry %d at %s (missing/empty file)",
                    idx, self.json_path,
                )
                continue
            try:
                duplicated_lines = int(value)
            except (TypeError, ValueError) as e:
                LOG.warning(
                    "Skipping DuDe internal entry %d at %s (value=%r not int: %s)",
                    idx, self.json_path, value, e,
                )
                continue
            entries.append(InternalDuplicationEntryDTO(
                file=file_path, value=duplicated_lines,
            ))
        LOG.info("Loaded %d DuDe internal entries from %s", len(entries), self.json_path)
        return entries
