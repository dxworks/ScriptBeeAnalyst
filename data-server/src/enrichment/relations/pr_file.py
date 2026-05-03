"""PR ↔ File — strength = number of linked Git commits in the PR that touch the file.

Source = PR number (string), target = file path. Per-(pr, file, commit) dedup
prevents rename+edit in the same commit from double-counting. Emits two
RelationFiles: lifetime (every linked commit) and recent (linked commits whose
author_date falls inside `ctx.recent_cutoff`). Depends on `pr.git_commits` being
populated by the project linker — empty when no GitHub project is loaded.
"""
from __future__ import annotations

from collections import defaultdict

from src.enrichment.models import Relation, RelationFile
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


class PullRequestFileExtractor:

    KIND = "pr.file"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        github = ctx.graph_data.get("github")
        if github is None:
            return []

        cutoff = ctx.recent_cutoff

        lifetime: dict[tuple[str, str], int] = defaultdict(int)
        recent: dict[tuple[str, str], int] = defaultdict(int)

        for pr in github.pull_request_registry.all:
            commits = getattr(pr, "git_commits", None) or []
            seen: set[tuple[str, str, str]] = set()
            pr_id = str(pr.number)
            for c in commits:
                d = ensure_aware(getattr(c, "author_date", None))
                in_recent = cutoff is not None and d is not None and d >= cutoff
                for ch in getattr(c, "changes", None) or []:
                    f = getattr(ch, "file", None)
                    if f is None:
                        continue
                    fid = _file_id(f)
                    if not fid:
                        continue
                    triple = (pr_id, fid, c.id)
                    if triple in seen:
                        continue
                    seen.add(triple)
                    pair = (pr_id, fid)
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
            source_kind="pr",
            source_id=pr_id,
            target_kind="file",
            target_id=fid,
            kind=kind,
            strength=float(count),
        )
        for (pr_id, fid), count in sorted(pairs.items(), key=lambda kv: -kv[1])
    ]
    return RelationFile(kind=kind, window=window, relations=relations)
