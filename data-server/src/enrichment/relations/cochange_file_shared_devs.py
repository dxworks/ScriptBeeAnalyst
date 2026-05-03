"""File↔File co-change weighted by # distinct devs touching both files.

Strength = number of distinct authors that have modified BOTH files
(file-pair-level shared developer set, NOT a per-commit count).

Mirrors `cochange.py` in skipping merge commits and bulk commits to keep the
coupling signal clean. The recent-window variant restricts the author set to
authors whose touches happened inside the recent window.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from src.enrichment.models import Relation, RelationFile
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


class FileSharedDevsCoChangeExtractor:
    """Two RelationFiles: lifetime + recent (recent restricts author touches)."""

    KIND = "cochange.file-file.shared-devs"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        cutoff = ctx.recent_cutoff
        max_files = ctx.config.cochange_max_files_per_commit

        # For each file, collect the set of author ids that touched it (with
        # date filtering for the recent window).
        lifetime_authors_per_file: dict[str, set[str]] = defaultdict(set)
        recent_authors_per_file: dict[str, set[str]] = defaultdict(set)
        # Track which file pairs were ever co-touched in a SAME commit so we
        # only emit edges for actual co-changes (matching cochange.py semantics).
        cochange_pairs: set[tuple[str, str]] = set()
        recent_cochange_pairs: set[tuple[str, str]] = set()

        for commit in git.git_commit_registry.all:
            if len(getattr(commit, "parents", []) or []) > 1:
                continue
            changes = getattr(commit, "changes", None) or []
            if not (2 <= len(changes) <= max_files):
                continue

            author = getattr(commit, "author", None)
            if author is None:
                continue
            aid = getattr(author, "id", None) or str(author)

            paths: list[str] = []
            for ch in changes:
                f = getattr(ch, "file", None)
                if f is None:
                    continue
                fid = _file_id(f)
                if fid:
                    paths.append(fid)
            unique_paths = sorted(set(paths))
            if len(unique_paths) < 2:
                continue

            d = ensure_aware(getattr(commit, "author_date", None) or getattr(commit, "committer_date", None))
            in_recent = cutoff is not None and d is not None and d >= cutoff

            for p in unique_paths:
                lifetime_authors_per_file[p].add(aid)
                if in_recent:
                    recent_authors_per_file[p].add(aid)

            for a, b in combinations(unique_paths, 2):
                cochange_pairs.add((a, b))
                if in_recent:
                    recent_cochange_pairs.add((a, b))

        return [
            _to_relation_file(
                self.KIND, "lifetime", cochange_pairs, lifetime_authors_per_file
            ),
            _to_relation_file(
                self.KIND, "recent", recent_cochange_pairs, recent_authors_per_file
            ),
        ]


def _to_relation_file(
    kind: str,
    window,
    pairs: set[tuple[str, str]],
    authors_per_file: dict[str, set[str]],
) -> RelationFile:
    rels: list[Relation] = []
    for a, b in pairs:
        shared = authors_per_file.get(a, set()) & authors_per_file.get(b, set())
        if not shared:
            continue
        rels.append(Relation(
            source_kind="file",
            source_id=a,
            target_kind="file",
            target_id=b,
            kind=kind,
            strength=float(len(shared)),
        ))
    rels.sort(key=lambda r: -r.strength)
    return RelationFile(kind=kind, window=window, relations=rels)
