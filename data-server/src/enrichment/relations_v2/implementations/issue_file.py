"""Issue ↔ File builder.

Subsumes the legacy ``ProjectLinker``'s commit-message regex step PLUS
the legacy ``relations/issue_file.py``: this single builder

1. Scans every commit message for any known issue key (regex match),
2. For each (issue, commit) pair, walks ``ChangeRegistry.by_commit[commit]``
   to find every file the commit touched,
3. Emits an :class:`Relation` per (issue, file) pair with strength =
   number of distinct linking commits.

Two windows emitted per pair: ``LIFETIME`` always; ``RECENT`` when the
host carries a recent cutoff. Per-(issue, file, commit) dedup prevents
rename+edit in the same commit from double-counting.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Iterable, Optional, Pattern

from src.enrichment.relations_v2 import Relation, RelationBuilder, WindowKind
from src.enrichment.relations_v2.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class IssueFileBuilder(RelationBuilder):
    name = "issue.file"
    relation_kind = "issue_file"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        issues = _safe_iter(getattr(graph, "issues", None))
        commits = _safe_iter(getattr(graph, "commits", None))
        if not issues or not commits:
            return

        # Build the issue-key regex (case-insensitive) over every known issue.
        issue_by_key = {
            issue.key.upper(): issue for issue in issues if getattr(issue, "key", None)
        }
        pattern = _build_issue_pattern(issue_by_key.keys())
        if pattern is None:
            return

        changes_by_commit = _changes_by_commit_index(graph)
        cutoff = getattr(graph, "recent_cutoff", None)

        lifetime: dict[tuple[Any, Any], int] = defaultdict(int)
        recent: dict[tuple[Any, Any], int] = defaultdict(int)

        for commit in commits:
            message = getattr(commit, "message", "") or ""
            if not message:
                continue
            matches = pattern.findall(message)
            if not matches:
                continue

            in_recent = cutoff is not None and _commit_in_window(commit, cutoff)
            commit_ref = commit.ref()

            # Per-commit issue keys (unique, uppercased).
            issue_keys_here = {m.upper() for m in matches}
            change_file_refs: list[Any] = []
            seen_files: set[Any] = set()
            for change in changes_by_commit(commit_ref):
                fref = getattr(change, "file_ref", None)
                if fref is None or fref in seen_files:
                    continue
                seen_files.add(fref)
                change_file_refs.append(fref)

            for key in issue_keys_here:
                issue = issue_by_key.get(key)
                if issue is None:
                    continue
                issue_ref = issue.ref()
                for file_ref in change_file_refs:
                    lifetime[(issue_ref, file_ref)] += 1
                    if in_recent:
                        recent[(issue_ref, file_ref)] += 1

        yield from _emit(self.relation_kind, WindowKind.LIFETIME, lifetime)
        if cutoff is not None:
            yield from _emit(self.relation_kind, WindowKind.RECENT, recent)


# ----------------------------------------------------------------------
# Helpers (shared by issue_file / issue_issue)
# ----------------------------------------------------------------------
def _safe_iter(reg: Any) -> list[Any]:
    if reg is None:
        return []
    try:
        return list(reg)
    except TypeError:
        return []


def _build_issue_pattern(keys: Iterable[str]) -> Optional[Pattern[str]]:
    escaped = [re.escape(k) for k in keys if k]
    if not escaped:
        return None
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)


def _changes_by_commit_index(graph: Any):
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _commit_ref: []
    by_commit = getattr(changes, "by_commit", None)
    if by_commit is not None:
        return lambda commit_ref: by_commit[commit_ref]

    def scan(commit_ref):
        return [ch for ch in changes if ch.commit_ref == commit_ref]

    return scan


def _commit_in_window(commit: Any, cutoff: Any) -> bool:
    d = getattr(commit, "author_date", None) or getattr(commit, "committer_date", None)
    if d is None or cutoff is None:
        return False
    try:
        return d >= cutoff
    except TypeError:
        return False


def _emit(
    relation_kind: str,
    window: WindowKind,
    pairs: dict[tuple[Any, Any], int],
) -> Iterable[Relation]:
    for (src, tgt), count in pairs.items():
        rid = Relation.canonical_id(src, tgt, relation_kind, window)
        yield Relation(
            id=rid,
            source=src,
            target=tgt,
            relation_kind=relation_kind,
            window=window,
            strength=float(count),
        )


__all__ = ["IssueFileBuilder"]
