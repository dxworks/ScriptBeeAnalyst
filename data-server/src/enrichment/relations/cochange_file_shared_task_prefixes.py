"""File↔File co-change weighted by # shared Jira project prefixes.

For each file, collect the set of Jira project prefixes (e.g. "ZEPPELIN" from
"ZEPPELIN-1234") via the file's commits' linked issues. Strength on a file
pair = |prefixes_a ∩ prefixes_b|.

Skips merge commits and bulk commits, mirroring `cochange.py`.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from src.enrichment.models import Relation, RelationFile
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


def _prefix_of(issue) -> str | None:
    key = getattr(issue, "key", None)
    if not key:
        return None
    return key.split("-", 1)[0] if "-" in key else key


class FileSharedTaskPrefixesCoChangeExtractor:

    KIND = "cochange.file-file.shared-task-prefixes"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []
        # No jira loaded -> no prefixes can ever match. Emit empty windows so
        # callers (and tests) see the kind regardless.
        jira_present = ctx.graph_data.get("jira") is not None

        cutoff = ctx.recent_cutoff
        max_files = ctx.config.cochange_max_files_per_commit

        lifetime_prefixes: dict[str, set[str]] = defaultdict(set)
        recent_prefixes: dict[str, set[str]] = defaultdict(set)
        cochange_pairs: set[tuple[str, str]] = set()
        recent_cochange_pairs: set[tuple[str, str]] = set()

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
            unique_paths = sorted(set(paths))
            if len(unique_paths) < 2:
                continue

            prefixes: set[str] = set()
            if jira_present:
                for issue in getattr(commit, "issues", None) or []:
                    p = _prefix_of(issue)
                    if p:
                        prefixes.add(p)

            d = ensure_aware(getattr(commit, "author_date", None) or getattr(commit, "committer_date", None))
            in_recent = cutoff is not None and d is not None and d >= cutoff

            for path in unique_paths:
                if prefixes:
                    lifetime_prefixes[path].update(prefixes)
                    if in_recent:
                        recent_prefixes[path].update(prefixes)
            for a, b in combinations(unique_paths, 2):
                cochange_pairs.add((a, b))
                if in_recent:
                    recent_cochange_pairs.add((a, b))

        return [
            _to_relation_file(self.KIND, "lifetime", cochange_pairs, lifetime_prefixes),
            _to_relation_file(self.KIND, "recent", recent_cochange_pairs, recent_prefixes),
        ]


def _to_relation_file(
    kind: str,
    window,
    pairs: set[tuple[str, str]],
    prefixes_per_file: dict[str, set[str]],
) -> RelationFile:
    rels: list[Relation] = []
    for a, b in pairs:
        shared = prefixes_per_file.get(a, set()) & prefixes_per_file.get(b, set())
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
