"""Transform Insider DTO rows into the canonical QualityIssue domain objects.

Implements §4 of communication/B4_sonar_insider/index_step_general.md.

Per orchestrator decision (B4):
  * `value` is the OCCURRENCE COUNT (heavy-tailed 1..116 in the Zeppelin run);
    we surface it as `occurrence_count` and do NOT normalise to a severity scale.
  * Insider does not provide line numbers, severity labels, messages or
    language tags; those fields stay None on the QualityIssue.
  * `path_prefix` is configurable (mirrors B2/B3 transformers): when Insider
    paths arrive without the project-id prefix the caller passes the prefix
    in and the transformer prepends it; if the path already begins with that
    prefix the function is a no-op so the same code handles both shapes.
  * IDs are stable per (file, rule) — the parser re-bins identical
    (file, rule) records by occurrence index inside that bin so re-runs that
    reorder the JSON array do not change ids.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, List, Optional

from src.common.quality_models import QualityIssue, QualityIssues
from src.logger import get_logger
from src.quality_miner.reader_dto.models import InsiderCodeSmellRowDTO

LOG = get_logger(__name__)


class InsiderQualityIssuesTransformer:
    """Build a QualityIssues container from a list of Insider DTO rows."""

    def __init__(
        self,
        rows: Iterable[InsiderCodeSmellRowDTO],
        path_prefix: Optional[str] = None,
        source: str = "insider",
    ):
        self.rows = list(rows)
        self.path_prefix = path_prefix
        self.source = source

    def transform(self) -> QualityIssues:
        # WHY: bin by (file, rule) so the per-bin index is stable across
        # JSON-array reorderings; the trailing array index in the original
        # plan would change every re-run when Insider's emission order shifts.
        per_bin_idx: dict[tuple[str, str], int] = defaultdict(int)
        issues: List[QualityIssue] = []
        for row in self.rows:
            file_path = _normalise_path(row.file, self.path_prefix)
            bin_key = (file_path, row.name)
            occurrence_idx = per_bin_idx[bin_key]
            per_bin_idx[bin_key] += 1
            issue_id = f"{self.source}:{file_path}:{row.name}:{occurrence_idx}"
            issues.append(QualityIssue(
                id=issue_id,
                rule_name=row.name,
                category=row.category,
                file_path=file_path,
                occurrence_count=row.value,
                source=self.source,
            ))
        LOG.info(
            "Built QualityIssues: %d issues from %d rows (source=%s, distinct files=%d)",
            len(issues), len(self.rows), self.source,
            len({i.file_path for i in issues}),
        )
        return QualityIssues(source=self.source, issues=issues)


def _normalise_path(raw_path: str, path_prefix: Optional[str]) -> str:
    """Normalise an Insider file path into the same convention as iglog/JaFax/Lizard/DuDe.

    Insider paths are POSIX, repo-relative-but-ALREADY-prefixed in the observed
    Zeppelin run (e.g. `zeppelin/file/src/.../FileInterpreter.java`). Per
    orchestrator decision the caller may pass `path_prefix` to prepend the
    project-id segment when the input lacks it; if the path already begins
    with that prefix the function is a no-op so the same code handles both
    shapes (mirrors B3's `_normalise_path`).
    """
    path = raw_path.replace("\\", "/").strip().lstrip("./").lstrip("/")
    if path_prefix:
        prefix = path_prefix.strip("/")
        if prefix and not path.startswith(prefix + "/"):
            path = f"{prefix}/{path}"
    return path
