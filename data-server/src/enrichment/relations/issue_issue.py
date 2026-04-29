"""Issue ↔ Issue.

Strength = native parent/child link weight 2 + shared-file overlap count
(weight 1 per shared file). Lifetime only — issue links are static.

`extras` carries `{"native_link": bool, "shared_files": <int>}` so the agent
can distinguish a structural link from a co-touch derivation.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from src.enrichment.models import Relation, RelationFile
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


_NATIVE_WEIGHT = 2.0


class IssueIssueExtractor:

    KIND = "issue.issue"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        jira = ctx.graph_data.get("jira")
        if jira is None:
            return []

        # Native parent/child edges, accumulated as (a, b) -> True (sorted keys).
        native: set[tuple[str, str]] = set()
        files_by_issue: dict[str, set[str]] = defaultdict(set)

        for issue in jira.issue_registry.all:
            key = getattr(issue, "key", None)
            if not key:
                continue
            parent = getattr(issue, "parent", None)
            if parent is not None and getattr(parent, "key", None):
                native.add(tuple(sorted((key, parent.key))))
            for child in getattr(issue, "children", None) or []:
                if getattr(child, "key", None):
                    native.add(tuple(sorted((key, child.key))))

            for c in getattr(issue, "git_commits", None) or []:
                for ch in getattr(c, "changes", None) or []:
                    f = getattr(ch, "file", None)
                    if f is None:
                        continue
                    fid = _file_id(f)
                    if fid:
                        files_by_issue[key].add(fid)

        # Shared-file overlap counts.
        shared_pairs: dict[tuple[str, str], int] = defaultdict(int)
        keys = sorted(files_by_issue.keys())
        for a, b in combinations(keys, 2):
            overlap = len(files_by_issue[a] & files_by_issue[b])
            if overlap > 0:
                shared_pairs[tuple(sorted((a, b)))] = overlap

        all_pairs: set[tuple[str, str]] = set(native) | set(shared_pairs.keys())
        relations = []
        for pair in all_pairs:
            a, b = pair
            shared = shared_pairs.get(pair, 0)
            is_native = pair in native
            strength = (_NATIVE_WEIGHT if is_native else 0.0) + float(shared)
            if strength <= 0:
                continue
            relations.append(Relation(
                source_kind="issue",
                source_id=a,
                target_kind="issue",
                target_id=b,
                kind=self.KIND,
                strength=strength,
                extras={"native_link": is_native, "shared_files": shared},
            ))

        relations.sort(key=lambda r: -r.strength)
        return [RelationFile(kind=self.KIND, window="lifetime", relations=relations)]
