"""File↔File field-access relation — Python port of dx's `FilesWithExtData`.

Implements §7 (data-access.file-file) of communication/B2_codeframe/index_step_general.md.

Source: `CodeReference` rows with kind="fieldAccess". Strength = sum of
weights. Self-loops dropped.
"""
from __future__ import annotations

from src.enrichment.models import Relation, RelationFile
from src.enrichment.tagger.base import TaggingContext


class FileDataAccessExtractor:
    """File-file field-access edges. Strength = number of fieldAccess references."""

    KIND = "data-access.file-file"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        cs = ctx.graph_data.get("code_structure")
        if cs is None:
            return []

        pairs: dict[tuple[str, str], int] = {}
        for ref in cs.reference_registry.all:
            if ref.kind != "fieldAccess":
                continue
            if ref.from_file_path == ref.to_file_path:
                continue
            key = (ref.from_file_path, ref.to_file_path)
            pairs[key] = pairs.get(key, 0) + ref.weight

        relations = [
            Relation(
                source_kind="file",
                source_id=a,
                target_kind="file",
                target_id=b,
                kind=self.KIND,
                strength=float(count),
            )
            for (a, b), count in sorted(pairs.items(), key=lambda kv: -kv[1])
        ]
        return [RelationFile(kind=self.KIND, window="lifetime", relations=relations)]
