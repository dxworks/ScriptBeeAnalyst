"""Author ↔ Author co-authorship — strength = number of files both authors changed.

Symmetric: each pair emitted once with sorted (source_id < target_id) so the
edge list stays half the size and the CSV is consistent with `cochange.file-file`.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from src.enrichment.models import Relation, RelationFile
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


class CoAuthorExtractor:

    KIND = "coauthor.author-author"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        cutoff = ctx.recent_cutoff

        lifetime_authors_per_file: dict[str, set[str]] = defaultdict(set)
        recent_authors_per_file: dict[str, set[str]] = defaultdict(set)

        for file_ in git.file_registry.all:
            fid = _file_id(file_)
            if not fid:
                continue
            for ch in file_.changes or []:
                c = getattr(ch, "commit", None)
                if c is None:
                    continue
                a = getattr(c, "author", None)
                if a is None:
                    continue
                aid = getattr(a, "id", None) or str(a)
                lifetime_authors_per_file[fid].add(aid)
                d = ensure_aware(getattr(c, "author_date", None))
                if cutoff is not None and d is not None and d >= cutoff:
                    recent_authors_per_file[fid].add(aid)

        return [
            _to_relation_file(self.KIND, "lifetime", lifetime_authors_per_file),
            _to_relation_file(self.KIND, "recent", recent_authors_per_file),
        ]


def _to_relation_file(kind, window, authors_per_file: dict[str, set[str]]) -> RelationFile:
    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for authors in authors_per_file.values():
        if len(authors) < 2:
            continue
        for a, b in combinations(sorted(authors), 2):
            pairs[(a, b)] += 1

    relations = [
        Relation(
            source_kind="author",
            source_id=a,
            target_kind="author",
            target_id=b,
            kind=kind,
            strength=float(count),
        )
        for (a, b), count in sorted(pairs.items(), key=lambda kv: -kv[1])
    ]
    return RelationFile(kind=kind, window=window, relations=relations)
