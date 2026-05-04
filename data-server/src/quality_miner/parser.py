"""Parser abstraction for quality-issue ingestion.

Implements §0 + §4 of communication/B4_sonar_insider/index_step_general.md.

The parser layer is intentionally thin (mirrors `codestructure_miner.parser`)
so a SonarQube parser can drop in later without touching the tagger,
overview or relation layers. Today only Insider ships — see
`quality_miner.linker.transformers.InsiderQualityIssuesTransformer`. To plug
in a new format:

  1. Add a new value to `QualityIssueFormat`.
  2. Implement a `QualityIssueParser` that returns a `QualityIssues`.
  3. Register it in `_PARSERS`.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, Protocol

from src.common.quality_models import QualityIssues
from src.quality_miner.linker.transformers import InsiderQualityIssuesTransformer
from src.quality_miner.reader_dto.loader import InsiderCodeSmellsJsonLoader


class QualityIssueFormat(str, Enum):
    INSIDER = "insider"        # Insider code-smells JSON
    SONARQUBE = "sonarqube"    # SonarQube issues export (future)


class QualityIssueParser(Protocol):
    """Strategy interface — every concrete parser implements `parse`."""

    def parse(
        self, path: str, path_prefix: Optional[str] = None,
    ) -> QualityIssues:
        ...


class InsiderParser:
    """Insider code-smells JSON parser."""

    def parse(
        self, path: str, path_prefix: Optional[str] = None,
    ) -> QualityIssues:
        rows = InsiderCodeSmellsJsonLoader(path).load()
        return InsiderQualityIssuesTransformer(
            rows, path_prefix=path_prefix, source="insider",
        ).transform()


_PARSERS: dict[QualityIssueFormat, QualityIssueParser] = {
    QualityIssueFormat.INSIDER: InsiderParser(),
}


def parse(
    path: str,
    fmt: QualityIssueFormat,
    path_prefix: Optional[str] = None,
) -> QualityIssues:
    """Parse a quality-tool artefact with the strategy registered for `fmt`.

    Raises NotImplementedError when the format has no parser registered yet
    (e.g. `QualityIssueFormat.SONARQUBE` until it ships).
    """
    parser = _PARSERS.get(fmt)
    if parser is None:
        raise NotImplementedError(
            f"No parser registered for format {fmt!r}. "
            f"Available: {sorted(p.value for p in _PARSERS)}"
        )
    return parser.parse(path, path_prefix=path_prefix)


def parse_insider(
    json_path: str,
    path_prefix: Optional[str] = None,
) -> QualityIssues:
    """Convenience helper for the most common path (Insider JSON)."""
    return parse(json_path, QualityIssueFormat.INSIDER, path_prefix=path_prefix)
