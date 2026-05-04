"""High-level entry point for ingesting DuDe outputs.

Implements §4-5 of communication/B3_dude/index_step_general.md.

Mirrors the codestructure_miner `parse(...)` shape: callers pass paths to the
two artefacts (either may be None if missing) and receive one canonical
`Duplication` object. Honeydew is intentionally not wired in this pass — see
communication/B3_dude/IMPLEMENTATION_NOTES.md.
"""
from __future__ import annotations

from typing import Optional

from src.common.duplication_models import Duplication
from src.dude_miner.linker.transformers import DudeDuplicationTransformer
from src.dude_miner.reader_dto.loader import (
    DudeExternalCsvLoader,
    DudeInternalJsonLoader,
)


def parse_dude(
    external_csv_path: Optional[str] = None,
    internal_json_path: Optional[str] = None,
    path_prefix: Optional[str] = None,
    source: str = "dude",
) -> Duplication:
    """Parse one or both DuDe artefacts into a Duplication container.

    Either argument may be None; in that case the corresponding registry on
    the returned Duplication will be empty. If both are None the result is
    an empty Duplication (the caller should usually skip storing it).
    """
    external_rows = []
    internal_entries = []

    if external_csv_path:
        external_rows = DudeExternalCsvLoader(external_csv_path).load()
    if internal_json_path:
        internal_entries = DudeInternalJsonLoader(internal_json_path).load()

    return DudeDuplicationTransformer(
        external_rows=external_rows,
        internal_entries=internal_entries,
        path_prefix=path_prefix,
        source=source,
    ).transform()
