"""CSV loaders for Lizard and Metrix++ outputs.

Implements §4 of communication/B1_lizard/index_step_general.md.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from src.lizard_miner.reader_dto.models import LizardRowDTO
from src.logger import get_logger

LOG = get_logger(__name__)


LIZARD_HEADER = ("NLOC", "CCN", "token", "PARAM", "length",
                 "location", "file", "function", "long_name", "start", "end")

METRIXPP_HEADER_PREFIX = ("file", "region", "type")


class LizardCsvLoader:
    """Reads Lizard's per-function CSV into a list of `LizardRowDTO`."""

    def __init__(self, csv_path: str):
        self.csv_path = Path(csv_path)

    def load(self) -> List[LizardRowDTO]:
        if not self.csv_path.exists():
            raise FileNotFoundError(f"Lizard CSV not found: {self.csv_path}")

        rows: List[LizardRowDTO] = []
        with self.csv_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                LOG.warning("Lizard CSV %s is empty", self.csv_path)
                return rows
            if not _is_lizard_header(reader.fieldnames):
                raise ValueError(
                    f"Unexpected CSV header for Lizard at {self.csv_path}: {reader.fieldnames}"
                )
            for line_no, raw in enumerate(reader, start=2):
                try:
                    rows.append(LizardRowDTO(
                        nloc=int(raw["NLOC"]),
                        ccn=int(raw["CCN"]),
                        token=int(raw["token"]),
                        param=int(raw["PARAM"]),
                        length=int(raw["length"]),
                        location=raw.get("location") or "",
                        file=raw["file"],
                        function=raw.get("function") or "",
                        long_name=raw.get("long_name") or "",
                        start=int(raw["start"]),
                        end=int(raw["end"]),
                    ))
                except (KeyError, TypeError, ValueError) as e:
                    LOG.warning(
                        "Skipping malformed Lizard row at %s:%d (%s)",
                        self.csv_path, line_no, e,
                    )
                    continue
        LOG.info("Loaded %d Lizard rows from %s", len(rows), self.csv_path)
        return rows


class MetrixppCsvLoader:
    """Tolerant Metrix++ CSV loader.

    Stub: vendored Metrix++ 1.7.3 fails on Python 3.11+ (uses removed
    `open(..., 'rU', ...)`), so the runner produces a header-only CSV.
    Returns [] on header-only input rather than crashing the pipeline.
    """

    def __init__(self, csv_path: str):
        self.csv_path = Path(csv_path)

    def load(self) -> list[dict]:
        if not self.csv_path.exists():
            raise FileNotFoundError(f"Metrix++ CSV not found: {self.csv_path}")

        with self.csv_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                LOG.warning("Metrix++ CSV %s is empty", self.csv_path)
                return []
            if not _looks_like_metrixpp_header(reader.fieldnames):
                raise ValueError(
                    f"Unexpected CSV header for Metrix++ at {self.csv_path}: {reader.fieldnames}"
                )
            rows = list(reader)
        if not rows:
            LOG.warning(
                "Metrix++ CSV %s is header-only; bundled Metrix++ 1.7.3 is "
                "broken on Python 3.11+, ingestion skipped",
                self.csv_path,
            )
        return rows


def _is_lizard_header(fields) -> bool:
    return tuple(fields) == LIZARD_HEADER


def _looks_like_metrixpp_header(fields) -> bool:
    if not fields or len(fields) < 3:
        return False
    return tuple(fields[:3]) == METRIXPP_HEADER_PREFIX
