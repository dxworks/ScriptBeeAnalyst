"""Issue ↔ File relation: issues already linked to commits → commit.changes → files.

Strength = number of distinct linked commits that touch the file (per
(issue, file, commit) triple, so a rename+edit in the same commit doesn't
double-count). Emits two RelationFiles: lifetime (every linked commit) and
recent (linked commits whose author_date falls inside `ctx.recent_cutoff`).

Depends on the project linker having populated `issue.git_commits` —
returns empty when no Jira project is loaded.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from src.enrichment.models import Relation, RelationFile
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


class IssueFileExtractor:

    KIND = "issue.file"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        jira = ctx.graph_data.get("jira")
        if jira is None:
            return []

        cutoff = ctx.recent_cutoff

        lifetime: dict[tuple[str, str], int] = defaultdict(int)
        recent: dict[tuple[str, str], int] = defaultdict(int)

        for issue in jira.issue_registry.all:
            commits = getattr(issue, "git_commits", None) or []
            # One count per (issue, file, commit) so multi-change rename+edit
            # commits don't double-count the same logical touch.
            seen: set[tuple[str, str, str]] = set()
            for c in commits:
                d = ensure_aware(getattr(c, "author_date", None))
                in_recent = cutoff is not None and d is not None and d >= cutoff
                for change in getattr(c, "changes", None) or []:
                    f = getattr(change, "file", None)
                    if f is None:
                        continue
                    fid = _file_id(f)
                    if not fid:
                        continue
                    key_with_commit = (issue.key, fid, c.id)
                    if key_with_commit in seen:
                        continue
                    seen.add(key_with_commit)
                    pair = (issue.key, fid)
                    lifetime[pair] += 1
                    if in_recent:
                        recent[pair] += 1

        return [
            _to_relation_file(self.KIND, "lifetime", lifetime),
            _to_relation_file(self.KIND, "recent", recent),
        ]


def _to_relation_file(kind, window, pairs) -> RelationFile:
    relations = [
        Relation(
            source_kind="issue",
            source_id=issue_key,
            target_kind="file",
            target_id=fid,
            kind=kind,
            strength=float(count),
        )
        for (issue_key, fid), count in sorted(pairs.items(), key=lambda kv: -kv[1])
    ]
    return RelationFile(kind=kind, window=window, relations=relations)
