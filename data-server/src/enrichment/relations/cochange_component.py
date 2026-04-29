"""Component ↔ Component co-change — aggregates `cochange.file-file` edges.

Each file-file edge maps to a component-component edge by resolving both
endpoints through `ComponentResolver`. Self-loops are dropped. Pairs are
emitted symmetrically with sorted (a, b) so the CSV stays half-sized.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from src.enrichment.components.resolver import ComponentResolver
from src.enrichment.models import Relation, RelationFile
from src.enrichment.tagger.base import TaggingContext


class ComponentCoChangeExtractor:

    KIND = "cochange.component-component"

    def __init__(self, resolver: ComponentResolver, file_relation_files: Iterable[RelationFile]):
        self._resolver = resolver
        self._file_relations = list(file_relation_files)

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        windows = {"lifetime": defaultdict(float), "recent": defaultdict(float)}

        for rf in self._file_relations:
            if rf.kind != "cochange.file-file":
                continue
            bucket = windows.get(rf.window)
            if bucket is None:
                continue
            for r in rf.relations:
                a = self._resolver.resolve(r.source_id)
                b = self._resolver.resolve(r.target_id)
                if a is None or b is None or a == b:
                    continue
                key = tuple(sorted((a, b)))
                bucket[key] += float(r.strength)

        return [
            _to_relation_file(self.KIND, "lifetime", windows["lifetime"]),
            _to_relation_file(self.KIND, "recent", windows["recent"]),
        ]


def _to_relation_file(kind, window, pairs) -> RelationFile:
    relations = [
        Relation(
            source_kind="component",
            source_id=a,
            target_kind="component",
            target_id=b,
            kind=kind,
            strength=round(float(strength), 4),
        )
        for (a, b), strength in sorted(pairs.items(), key=lambda kv: -kv[1])
    ]
    return RelationFile(kind=kind, window=window, relations=relations)
