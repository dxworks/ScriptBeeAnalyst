"""Per-file rule violations ingested from Insider (Sonar later).

Implements §3 of communication/B4_sonar_insider/index_step_general.md, with
the schema corrections from index_step_data_format.md (Insider records carry
only `name`/`category`/`file`/`value` and `value` is an OCCURRENCE COUNT —
not a severity level — so we surface it as `occurrence_count` and do NOT
normalise it to a severity scale).

A `QualityIssue` is one rule violation reported by a quality tool for one
file. Insider does not provide line numbers, severity labels, messages or
language tags, so those fields are Optional and stay `None` for Insider rows;
they exist on the model so a future Sonar parser can populate them without a
schema change.

All Pydantic plain models — picklable without __reduce__ shims, also dump
cleanly to JSONB for the Supabase cache.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.common.registries import AbstractRegistry


class QualityIssue(BaseModel):
    """One rule violation reported by a quality tool for one file.

    For Insider, `occurrence_count` is the raw `value` field, i.e. the number
    of times the rule fires inside the file (heavy-tailed 1..116 in the
    Zeppelin run). It is NOT a 1..5 severity ordinal — see
    communication/B4_sonar_insider/index_step_data_format.md §5. SonarQube
    (when added) will populate `severity_label` instead and may leave
    `occurrence_count=1` per record.
    """
    model_config = ConfigDict(frozen=False)

    id: str  # synthesised primary key — stable per (source, file, rule, idx)
    rule_name: str  # raw rule name from the tool (Insider keeps spaces, e.g. "Stub Implementer")
    category: str   # rule family/bucket as the tool reports it (Insider: "Inheritance", "Traceability", ...)
    file_path: str  # repo-relative POSIX path (already prefixed when the tool emits it that way)
    occurrence_count: int  # raw `value`; Insider = #firings inside the file, Sonar = 1 per record
    source: str = "insider"  # "insider" or "sonarqube" — selects which parser produced this row
    # Sonar-only fields. Always None for Insider; reserved here so a future
    # Sonar parser can populate them without a model migration.
    severity_label: Optional[str] = None  # e.g. "BLOCKER" / "CRITICAL" / "MAJOR" / "MINOR" / "INFO"
    message: Optional[str] = None
    line_number: Optional[int] = None
    language: Optional[str] = None


class QualityIssueRegistry(AbstractRegistry[QualityIssue, str]):
    """Keyed by `id` so multiple rows for the same file/rule coexist."""

    def get_id(self, entity: QualityIssue) -> str:
        return entity.id


class QualityIssues(BaseModel):
    """Container stashed at `graph_data['quality_issues']`.

    Keeps the raw issue list plus a per-file index so taggers / endpoints can
    skip an O(N) scan to look up a file's rule firings.
    """
    model_config = ConfigDict(frozen=False)

    source: str = "insider"
    issues: List[QualityIssue] = Field(default_factory=list)

    @property
    def by_file(self) -> Dict[str, List[QualityIssue]]:
        """Group issues by `file_path`. Recomputed on access (cheap; Zeppelin = 419 rows)."""
        out: Dict[str, List[QualityIssue]] = {}
        for issue in self.issues:
            out.setdefault(issue.file_path, []).append(issue)
        return out

    @property
    def file_paths(self) -> set[str]:
        return {i.file_path for i in self.issues}
