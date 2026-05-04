"""Parser abstraction for code-structure ingestion.

Implements §4 of communication/B2_codeframe/index_step_general.md.

The parser layer is intentionally thin so a CodeFrame parser can drop in
later without touching the relation extractors or the domain models. Today
only the JaFax (FAMIX/Java) implementation ships — see
`codestructure_miner.linker.transformers.JaFaxTransformer`. To plug in a new
format:

  1. Add a new value to `CodeStructureFormat`.
  2. Implement a `CodeStructureParser` that returns a `CodeStructureProject`.
  3. Register it in `_PARSERS`.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, Protocol

from src.codestructure_miner.linker.transformers import JaFaxTransformer
from src.codestructure_miner.reader_dto.loader import JaFaxLayoutLoader
from src.common.codestructure_models import CodeStructureProject


class CodeStructureFormat(str, Enum):
    JAFAX = "jafax"           # FAMIX-style Java JSON layout
    CODEFRAME = "codeframe"   # Tree-sitter multi-language JSONL (future)


class CodeStructureParser(Protocol):
    """Strategy interface — every concrete parser implements `parse`."""

    def parse(
        self, path: str, path_prefix: Optional[str] = None,
    ) -> CodeStructureProject:
        ...


class JaFaxParser:
    """JaFax FAMIX layout JSON parser."""

    def parse(
        self, path: str, path_prefix: Optional[str] = None,
    ) -> CodeStructureProject:
        entities = JaFaxLayoutLoader(path).load()
        return JaFaxTransformer(entities, path_prefix=path_prefix, source="jafax").transform()


_PARSERS: dict[CodeStructureFormat, CodeStructureParser] = {
    CodeStructureFormat.JAFAX: JaFaxParser(),
}


def parse(
    path: str,
    fmt: CodeStructureFormat,
    path_prefix: Optional[str] = None,
) -> CodeStructureProject:
    """Parse a code-structure file with the strategy registered for `fmt`.

    Raises NotImplementedError when the format has no parser registered yet
    (e.g. `CodeStructureFormat.CODEFRAME` until B2.x lands).
    """
    parser = _PARSERS.get(fmt)
    if parser is None:
        raise NotImplementedError(
            f"No parser registered for format {fmt!r}. "
            f"Available: {sorted(p.value for p in _PARSERS)}"
        )
    return parser.parse(path, path_prefix=path_prefix)
