"""File↔File co-change relation — Python port of dx's FilesWithSharedCommits.

Strength = number of commits that touched both files. Skips merge commits and
bulk commits (the latter are near-useless signal: they typically co-touch
hundreds of unrelated files and would dominate the edge list).
"""
from __future__ import annotations

from itertools import combinations
from typing import Optional

from src.enrichment.models import Relation, RelationFile
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


class FileCoChangeExtractor:
    """Produces two RelationFile rows: lifetime + recent."""

    KIND = "cochange.file-file"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        lifetime_pairs: dict[tuple[str, str], int] = {}
        recent_pairs: dict[tuple[str, str], int] = {}
        cutoff = ctx.recent_cutoff
        max_files = ctx.config.cochange_max_files_per_commit

        for commit in git.git_commit_registry.all:
            if len(getattr(commit, "parents", []) or []) > 1:
                continue
            changes = getattr(commit, "changes", None) or []
            if not (2 <= len(changes) <= max_files):
                continue

            paths: list[str] = []
            for ch in changes:
                f = getattr(ch, "file", None)
                if f is None:
                    continue
                fid = _file_id(f)
                if fid:
                    paths.append(fid)
            if len(paths) < 2:
                continue

            # Dedup intra-commit (a single commit shouldn't double-count a pair
            # even if the file appears in multiple changes e.g. rename+edit).
            unique_paths = sorted(set(paths))
            in_recent = cutoff is not None and (commit.author_date or commit.committer_date) and (commit.author_date or commit.committer_date) >= cutoff

            for a, b in combinations(unique_paths, 2):
                key = (a, b)
                lifetime_pairs[key] = lifetime_pairs.get(key, 0) + 1
                if in_recent:
                    recent_pairs[key] = recent_pairs.get(key, 0) + 1

        return [
            _to_relation_file(self.KIND, "lifetime", lifetime_pairs),
            _to_relation_file(self.KIND, "recent", recent_pairs),
        ]


def _to_relation_file(kind: str, window, pairs: dict[tuple[str, str], int]) -> RelationFile:
    relations = [
        Relation(
            source_kind="file",
            source_id=a,
            target_kind="file",
            target_id=b,
            kind=kind,
            strength=float(count),
        )
        for (a, b), count in sorted(pairs.items(), key=lambda kv: -kv[1])
    ]
    return RelationFile(kind=kind, window=window, relations=relations)
