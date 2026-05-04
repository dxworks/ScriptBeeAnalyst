"""File↔File inheritance / interface relation — Python port of
`FilesWithHierarchyRelations`.

Implements §7 (hierarchy.file-file) of communication/B2_codeframe/index_step_general.md.

Source: `CodeReference` rows with kind ∈ {"inheritance", "interface"}.
Strength = number of hierarchy edges between the two files. Self-loops
dropped (a class extending another in the same file isn't an inter-file edge).
"""
from __future__ import annotations

from src.enrichment.models import Relation, RelationFile
from src.enrichment.tagger.base import TaggingContext


class FileHierarchyExtractor:
    """File-file inheritance/interface edges. Strength = number of hierarchy refs."""

    KIND = "hierarchy.file-file"

    HIERARCHY_KINDS = ("inheritance", "interface")

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        cs = ctx.graph_data.get("code_structure")
        if cs is None:
            return []

        pairs: dict[tuple[str, str], int] = {}
        for ref in cs.reference_registry.all:
            if ref.kind not in self.HIERARCHY_KINDS:
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
