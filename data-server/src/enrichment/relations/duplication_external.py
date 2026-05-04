"""File↔File external duplication relation — Python port of dx's `FilesWithExternalDuplicationRelations`.

Implements §7 (duplication.file-file.external) of communication/B3_dude/index_step_general.md.

Source: `Duplication.external_pairs` from DuDe. Strength = aggregated
`total_block_length` (sum of cleaned-code lines across all duplicated blocks
between the pair). `extras` carries the `block_count` so the agent can tell
"one big duplicated block" from "many small ones". Pairs whose two files
share an immediate parent directory are *excluded* here — they belong to the
sibling extractor instead.
"""
from __future__ import annotations

import posixpath

from src.enrichment.models import Relation, RelationFile
from src.enrichment.tagger.base import TaggingContext


class ExternalDuplicationExtractor:
    """File-file cross-directory duplication. Strength = total duplicated lines."""

    KIND = "duplication.file-file.external"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        dup = ctx.graph_data.get("duplication")
        if dup is None or not dup.external_pairs:
            return []

        relations: list[Relation] = []
        for pair in dup.external_pairs:
            # WHY: a pair is "sibling" iff both files live in the same immediate
            # parent directory; everything else is external. The split is here
            # (not in the parser) so the rule can evolve without re-ingesting.
            if posixpath.dirname(pair.file_a_path) == posixpath.dirname(pair.file_b_path):
                continue
            relations.append(Relation(
                source_kind="file",
                source_id=pair.file_a_path,
                target_kind="file",
                target_id=pair.file_b_path,
                kind=self.KIND,
                strength=float(pair.total_block_length),
                extras={"block_count": pair.block_count},
            ))
        relations.sort(key=lambda r: -r.strength)
        return [RelationFile(kind=self.KIND, window="lifetime", relations=relations)]
