"""Per-file internal-duplication summary edges — port of dx's internal duplication metric.

Implements §7 (duplication.file-file.internal-summary) of
communication/B3_dude/index_step_general.md.

Source: `Duplication.internal_by_file` from DuDe (one scalar per file =
duplicated lines inside that file). Internal duplication has no second
endpoint, so each file is emitted as a degenerate self-loop edge:
`source = target = file_path`, `strength = duplicated_lines`. This shape lets
the AI agent treat internal duplication uniformly with the file-pair edges
(`get_relation_edges(kind="duplication.file-file.internal-summary")`).
"""
from __future__ import annotations

from src.enrichment.models import Relation, RelationFile
from src.enrichment.tagger.base import TaggingContext


class InternalDuplicationSummaryExtractor:
    """Per-file internal duplication scalar. Strength = duplicated lines inside the file."""

    KIND = "duplication.file-file.internal-summary"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        dup = ctx.graph_data.get("duplication")
        if dup is None or not dup.internal_by_file:
            return []

        relations: list[Relation] = [
            Relation(
                source_kind="file",
                source_id=file_path,
                target_kind="file",
                target_id=file_path,
                kind=self.KIND,
                strength=float(value),
            )
            for file_path, value in dup.internal_by_file.items()
        ]
        relations.sort(key=lambda r: -r.strength)
        return [RelationFile(kind=self.KIND, window="lifetime", relations=relations)]
