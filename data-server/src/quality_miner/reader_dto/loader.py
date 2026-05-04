"""Loader for Insider's `<projectId>-code_smells.json`.

Implements §4 of communication/B4_sonar_insider/index_step_general.md.

Tolerant per-row: malformed entries are logged and skipped, but
filesystem-level problems (missing file, unreadable JSON, non-list root)
raise so the processor surfaces them instead of silently producing an empty
dataset.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from src.logger import get_logger
from src.quality_miner.reader_dto.models import InsiderCodeSmellRowDTO

LOG = get_logger(__name__)


class InsiderCodeSmellsJsonLoader:
    """Reads Insider's code-smells JSON array.

    The file is a top-level list of objects with keys {name, category, file,
    value}. The loader rejects malformed entries at row level (warning + skip)
    so one bad record does not poison the rest of the ingest.
    """

    def __init__(self, json_path: str):
        self.json_path = Path(json_path)

    def load(self) -> List[InsiderCodeSmellRowDTO]:
        if not self.json_path.exists():
            raise FileNotFoundError(f"Insider code-smells JSON not found: {self.json_path}")

        with self.json_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError(
                f"Insider code-smells JSON must be a top-level list at {self.json_path}; "
                f"got {type(data).__name__}"
            )

        rows: List[InsiderCodeSmellRowDTO] = []
        for idx, raw in enumerate(data):
            if not isinstance(raw, dict):
                LOG.warning(
                    "Skipping Insider entry %d at %s (not an object: %r)",
                    idx, self.json_path, type(raw).__name__,
                )
                continue
            name = raw.get("name")
            category = raw.get("category")
            file_path = raw.get("file")
            value = raw.get("value")
            if not isinstance(name, str) or not name.strip():
                LOG.warning(
                    "Skipping Insider entry %d at %s (missing/empty name)",
                    idx, self.json_path,
                )
                continue
            if not isinstance(category, str) or not category.strip():
                LOG.warning(
                    "Skipping Insider entry %d at %s (missing/empty category)",
                    idx, self.json_path,
                )
                continue
            if not isinstance(file_path, str) or not file_path.strip():
                LOG.warning(
                    "Skipping Insider entry %d at %s (missing/empty file)",
                    idx, self.json_path,
                )
                continue
            try:
                value_int = int(value)
            except (TypeError, ValueError) as e:
                LOG.warning(
                    "Skipping Insider entry %d at %s (value=%r not int: %s)",
                    idx, self.json_path, value, e,
                )
                continue
            rows.append(InsiderCodeSmellRowDTO(
                name=name.strip(),
                category=category.strip(),
                file=file_path.strip(),
                value=value_int,
            ))
        LOG.info("Loaded %d Insider code-smell rows from %s", len(rows), self.json_path)
        return rows
