"""File↔File sibling duplication relation — Python port of dx's `FilesWithSiblingDuplicationRelations`.

Implements §7 (duplication.file-file.sibling) of communication/B3_dude/index_step_general.md.

Source: `Duplication.external_pairs` from DuDe, *filtered* to pairs whose two
files share the same immediate parent directory. Strength is identical to
the external extractor (total duplicated lines). The complementary pairs are
emitted by `ExternalDuplicationExtractor`.
"""
from __future__ import annotations

import posixpath

from src.enrichment.models import Relation, RelationFile
from src.enrichment.tagger.base import TaggingContext


class SiblingDuplicationExtractor:
    """File-file same-directory duplication. Strength = total duplicated lines."""

    KIND = "duplication.file-file.sibling"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        dup = ctx.graph_data.get("duplication")
        if dup is None or not dup.external_pairs:
            return []

        relations: list[Relation] = []
        for pair in dup.external_pairs:
            # WHY: a pair is "sibling" iff both files live in the same immediate
            # parent directory; this extractor is the inverse filter of the
            # external one.
            if posixpath.dirname(pair.file_a_path) != posixpath.dirname(pair.file_b_path):
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
