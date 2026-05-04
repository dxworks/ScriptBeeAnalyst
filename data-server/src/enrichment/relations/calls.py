"""File↔File method-call relation — Python port of dx's `FilesWithExtCalls`.

Implements §7 (calls.file-file) of communication/B2_codeframe/index_step_general.md.

Source: `CodeReference` rows with kind="call". Strength = sum of weights
(each call contributes 1; aggregated per source/target file pair). Self-loops
(file calls itself) are dropped — they carry no inter-file coupling signal.
"""
from __future__ import annotations

from src.enrichment.models import Relation, RelationFile
from src.enrichment.tagger.base import TaggingContext


class FileCallsExtractor:
    """File-file method-call edges. Strength = number of call references."""

    KIND = "calls.file-file"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        cs = ctx.graph_data.get("code_structure")
        if cs is None:
            return []

        pairs: dict[tuple[str, str], int] = {}
        for ref in cs.reference_registry.all:
            if ref.kind != "call":
                continue
            if ref.from_file_path == ref.to_file_path:
                continue
            key = (ref.from_file_path, ref.to_file_path)
            pairs[key] = pairs.get(key, 0) + ref.weight

        return [_to_relation_file(self.KIND, pairs)]


def _to_relation_file(kind: str, pairs: dict[tuple[str, str], int]) -> RelationFile:
    relations = [
        Relation(
            source_kind="file",
            source_id=a,
            target_kind="file",
            target_id=b,
            kind=kind,
            strength=float(count),
        )
        for (a, b), count in sorted(pairs.items(), key=lambda kv: -kv[1])
    ]
    return RelationFile(kind=kind, window="lifetime", relations=relations)
