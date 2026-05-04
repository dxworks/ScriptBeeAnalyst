"""File↔File aggregate coupling relation — Python port of dx's `FilesWithCoupling`.

Implements §7 (coupling.file-file) of communication/B2_codeframe/index_step_general.md.

Source: every `CodeReference` row regardless of kind. Strength = raw sum of
typed reference counts (orchestrator decision: raw sum, no per-kind
normalisation). Self-loops dropped — same rule as the per-kind extractors.
The per-kind breakdown is available in `extras` on each Relation row so the
agent can explain what dominates the coupling.
"""
from __future__ import annotations

from src.enrichment.models import Relation, RelationFile
from src.enrichment.tagger.base import TaggingContext


class FileCouplingExtractor:
    """All structural couplings combined. Strength = sum of typed reference counts."""

    KIND = "coupling.file-file"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        cs = ctx.graph_data.get("code_structure")
        if cs is None:
            return []

        pairs: dict[tuple[str, str], dict[str, int]] = {}
        for ref in cs.reference_registry.all:
            if ref.from_file_path == ref.to_file_path:
                continue
            key = (ref.from_file_path, ref.to_file_path)
            bucket = pairs.setdefault(key, {})
            bucket[ref.kind] = bucket.get(ref.kind, 0) + ref.weight

        relations: list[Relation] = []
        for (a, b), kinds in sorted(
            pairs.items(),
            key=lambda kv: -sum(kv[1].values()),
        ):
            total = sum(kinds.values())
            relations.append(Relation(
                source_kind="file",
                source_id=a,
                target_kind="file",
                target_id=b,
                kind=self.KIND,
                strength=float(total),
                extras={"breakdown": dict(kinds)},
            ))
        return [RelationFile(kind=self.KIND, window="lifetime", relations=relations)]
