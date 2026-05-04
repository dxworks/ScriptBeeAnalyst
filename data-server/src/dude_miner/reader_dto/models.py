"""DTOs matching the on-disk DuDe formats.

Implements §4 of communication/B3_dude/index_step_general.md.

DuDe emits two artefacts per project:
  - `<repo>-external_duplication.csv` — *headerless*, three columns:
      file_a, file_b, block_length (one row per duplicated block).
  - `<repo>-internal_duplication.json` — JSON array of per-file objects with
      keys {file, name, category, value}; `name` and `category` are constants
      in the spec and are dropped here (orchestrator decision §B3).
"""
from __future__ import annotations

from pydantic import BaseModel


class ExternalDuplicationRowDTO(BaseModel):
    """One row of `<repo>-external_duplication.csv` (one duplicated block)."""
    file_a: str
    file_b: str
    block_length: int  # in cleaned-code lines (DuDe `min.length=30`)


class InternalDuplicationEntryDTO(BaseModel):
    """One entry of `<repo>-internal_duplication.json` (per-file scalar)."""
    file: str
    value: int  # duplicated lines within this file
