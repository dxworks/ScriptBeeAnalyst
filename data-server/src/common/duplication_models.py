"""Per-pair external + per-file internal duplication ingested from DuDe.

Implements §3 of communication/B3_dude/index_step_general.md.

A `DuplicationPair` aggregates *all* DuDe duplicated blocks reported for one
unordered file pair: `total_block_length` is the sum across rows (lines) and
`block_count` is the number of rows. `kind` distinguishes EXTERNAL (cross-
directory) from SIBLING (same immediate parent directory) — sibling-vs-external
classification is performed downstream by the relation extractors, not the
parser.

A `DuplicationInternal` is a per-file scalar (duplicated lines inside one
file) sourced from DuDe's internal JSON; the constant `name` and `category`
keys present in the JSON are dropped at parse time because they carry no
information.

All Pydantic plain models — picklable without __reduce__ shims, also dump
cleanly to JSONB for the Supabase cache.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List

from pydantic import BaseModel, ConfigDict, Field


class DuplicationKind(str, Enum):
    """DuDe duplication relationship kinds (orchestrator decision §B3)."""
    EXTERNAL = "external"   # files in different immediate parent directories
    SIBLING = "sibling"     # files in the same immediate parent directory
    INTERNAL = "internal"   # within a single file


class DuplicationPair(BaseModel):
    """One unordered file pair with the aggregated DuDe block-length stats.

    Pair key is canonicalised by sorting (file_a_path, file_b_path) lexically
    so `(a, b)` and `(b, a)` collapse onto the same pair (DuDe's external CSV
    is symmetric; only one orientation is observed in practice but the parser
    canonicalises defensively).
    """
    model_config = ConfigDict(frozen=False)

    file_a_path: str
    file_b_path: str
    total_block_length: int   # sum(block_length) across rows for this pair (lines)
    block_count: int          # number of duplicated blocks reported for this pair
    kind: DuplicationKind = DuplicationKind.EXTERNAL


class DuplicationInternal(BaseModel):
    """Per-file internal duplication summary (lines duplicated *within* a file)."""
    model_config = ConfigDict(frozen=False)

    file_path: str
    duplicated_lines: int


class Duplication(BaseModel):
    """Container stashed at `graph_data['duplication']`.

    `external_pairs` is the aggregated, canonicalised set of file-pair edges.
    `internal_by_file` maps file_path → duplicated_lines (one entry per file
    that DuDe reported internal duplication for).
    """
    model_config = ConfigDict(frozen=False)

    source: str = "dude"
    external_pairs: List[DuplicationPair] = Field(default_factory=list)
    internal_by_file: Dict[str, int] = Field(default_factory=dict)
