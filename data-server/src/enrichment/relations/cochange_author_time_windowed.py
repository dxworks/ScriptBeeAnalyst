"""Author↔Author concurrent commits.

Strength = number of (commit_a, commit_b) pairs where commit_a is by author A,
commit_b is by author B, and the two author-dates fall within
`cfg.time_windowed_cochange_hours` of each other (any files).

The signal proxies "people working in the same time-frame" — useful for
detecting on-call buddies, pair-programming patterns, or spike-coordination.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import timedelta
from itertools import combinations

from src.enrichment.models import Relation, RelationFile
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext


class AuthorTimeWindowedExtractor:

    KIND = "cochange.author-author.time-windowed"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        cutoff = ctx.recent_cutoff
        delta = timedelta(hours=ctx.config.time_windowed_cochange_hours)

        # author_id -> sorted list of commit_dates.
        dates_by_author: dict[str, list] = defaultdict(list)
        for author in git.account_registry.all:
            aid = getattr(author, "id", None) or str(author)
            for c in getattr(author, "commits", None) or []:
                d = ensure_aware(getattr(c, "author_date", None))
                if d is None:
                    continue
                dates_by_author[aid].append(d)
        for aid in dates_by_author:
            dates_by_author[aid].sort()

        aids = sorted(dates_by_author.keys())

        lifetime_pairs: dict[tuple[str, str], int] = defaultdict(int)
        recent_pairs: dict[tuple[str, str], int] = defaultdict(int)

        for a, b in combinations(aids, 2):
            ev_a = dates_by_author[a]
            ev_b = dates_by_author[b]
            for da in ev_a:
                lo = bisect_left(ev_b, da - delta)
                hi = bisect_right(ev_b, da + delta)
                if hi <= lo:
                    continue
                lifetime_pairs[(a, b)] += hi - lo
                if cutoff is not None and da >= cutoff:
                    # Count only partner commits that are also recent.
                    rec_lo = bisect_left(ev_b, max(da - delta, cutoff))
                    rec_hi = bisect_right(ev_b, da + delta)
                    if rec_hi > rec_lo:
                        recent_pairs[(a, b)] += rec_hi - rec_lo

        return [
            _to_relation_file(self.KIND, "lifetime", lifetime_pairs),
            _to_relation_file(self.KIND, "recent", recent_pairs),
        ]


def _to_relation_file(kind: str, window, pairs: dict[tuple[str, str], int]) -> RelationFile:
    rels = [
        Relation(
            source_kind="author",
            source_id=a,
            target_kind="author",
            target_id=b,
            kind=kind,
            strength=float(count),
        )
        for (a, b), count in sorted(pairs.items(), key=lambda kv: -kv[1])
        if count > 0
    ]
    return RelationFile(kind=kind, window=window, relations=rels)
