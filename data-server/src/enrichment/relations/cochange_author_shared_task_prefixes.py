"""Author↔Author co-change weighted by # shared Jira project prefixes.

Strength = number of distinct Jira project prefixes both authors have touched
through their commits' linked issues. Lifetime + recent (recent restricts the
considered commits to those inside the window).
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from src.enrichment.models import Relation, RelationFile
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext


def _prefix_of(issue) -> str | None:
    key = getattr(issue, "key", None)
    if not key:
        return None
    return key.split("-", 1)[0] if "-" in key else key


class AuthorSharedTaskPrefixesExtractor:

    KIND = "cochange.author-author.shared-task-prefixes"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []
        if ctx.graph_data.get("jira") is None:
            # Without jira no prefixes are populated. Emit empty windows so
            # the kind is discoverable.
            return [
                RelationFile(kind=self.KIND, window="lifetime", relations=[]),
                RelationFile(kind=self.KIND, window="recent", relations=[]),
            ]

        cutoff = ctx.recent_cutoff

        lifetime_prefixes: dict[str, set[str]] = defaultdict(set)
        recent_prefixes: dict[str, set[str]] = defaultdict(set)

        # Walk authors via the registry (cheaper than re-deriving from commits).
        for author in git.account_registry.all:
            aid = getattr(author, "id", None) or str(author)
            for c in getattr(author, "commits", None) or []:
                issues = getattr(c, "issues", None) or []
                if not issues:
                    continue
                d = ensure_aware(getattr(c, "author_date", None))
                in_recent = cutoff is not None and d is not None and d >= cutoff
                for issue in issues:
                    p = _prefix_of(issue)
                    if not p:
                        continue
                    lifetime_prefixes[aid].add(p)
                    if in_recent:
                        recent_prefixes[aid].add(p)

        return [
            _to_relation_file(self.KIND, "lifetime", lifetime_prefixes),
            _to_relation_file(self.KIND, "recent", recent_prefixes),
        ]


def _to_relation_file(kind, window, prefixes_per_author: dict[str, set[str]]) -> RelationFile:
    aids = sorted(prefixes_per_author.keys())
    rels: list[Relation] = []
    for a, b in combinations(aids, 2):
        shared = prefixes_per_author[a] & prefixes_per_author[b]
        if not shared:
            continue
        rels.append(Relation(
            source_kind="author",
            source_id=a,
            target_kind="author",
            target_id=b,
            kind=kind,
            strength=float(len(shared)),
        ))
    rels.sort(key=lambda r: -r.strength)
    return RelationFile(kind=kind, window=window, relations=rels)
