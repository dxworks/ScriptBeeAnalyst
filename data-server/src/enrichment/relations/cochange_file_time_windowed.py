"""File↔File co-change inside a time window.

Counts pairs of *distinct* commits (one touching each file) whose author dates
fall within `cfg.time_windowed_cochange_hours` of each other. Same-commit
co-changes are intentionally excluded — that signal already lives in
`cochange.file-file`.

Skips merge commits and bulk commits, matching `cochange.py`. Two-pointer scan
over per-file sorted commit-date arrays gives O((|A|+|B|)·k) per pair where k
is the average number of partner commits inside the window.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import timedelta
from itertools import combinations

from src.enrichment.models import Relation, RelationFile
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


class FileTimeWindowedCoChangeExtractor:

    KIND = "cochange.file-file.time-windowed"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        cutoff = ctx.recent_cutoff
        max_files = ctx.config.cochange_max_files_per_commit
        delta = timedelta(hours=ctx.config.time_windowed_cochange_hours)

        # commit_id -> (date, [file_ids]) for commits that pass the merge/bulk filters.
        # Per-file sorted (date, commit_id) arrays.
        file_events: dict[str, list[tuple]] = defaultdict(list)

        for commit in git.git_commit_registry.all:
            if len(getattr(commit, "parents", []) or []) > 1:
                continue
            changes = getattr(commit, "changes", None) or []
            if not (1 <= len(changes) <= max_files):
                continue
            d = ensure_aware(getattr(commit, "author_date", None) or getattr(commit, "committer_date", None))
            if d is None:
                continue
            seen: set[str] = set()
            for ch in changes:
                f = getattr(ch, "file", None)
                if f is None:
                    continue
                fid = _file_id(f)
                if fid and fid not in seen:
                    seen.add(fid)
                    file_events[fid].append((d, commit.id))

        # Sort each file's events by date once.
        for fid in file_events:
            file_events[fid].sort(key=lambda kv: kv[0])

        files = sorted(file_events.keys())

        lifetime_pairs: dict[tuple[str, str], int] = defaultdict(int)
        recent_pairs: dict[tuple[str, str], int] = defaultdict(int)

        for a, b in combinations(files, 2):
            ev_a = file_events[a]
            ev_b = file_events[b]
            dates_b = [d for d, _ in ev_b]
            for da, cid_a in ev_a:
                lo = bisect_left(dates_b, da - delta)
                hi = bisect_right(dates_b, da + delta)
                for j in range(lo, hi):
                    db, cid_b = ev_b[j]
                    if cid_b == cid_a:
                        continue  # exclude same-commit pairs
                    lifetime_pairs[(a, b)] += 1
                    if cutoff is not None and da >= cutoff and db >= cutoff:
                        recent_pairs[(a, b)] += 1

        return [
            _to_relation_file(self.KIND, "lifetime", lifetime_pairs),
            _to_relation_file(self.KIND, "recent", recent_pairs),
        ]


def _to_relation_file(kind: str, window, pairs: dict[tuple[str, str], int]) -> RelationFile:
    rels = [
        Relation(
            source_kind="file",
            source_id=a,
            target_kind="file",
            target_id=b,
            kind=kind,
            strength=float(count),
        )
        for (a, b), count in sorted(pairs.items(), key=lambda kv: -kv[1])
        if count > 0
    ]
    return RelationFile(kind=kind, window=window, relations=rels)
