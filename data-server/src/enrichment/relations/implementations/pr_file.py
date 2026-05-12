"""PR ↔ File builder.

Subsumes the legacy ``ProjectLinker``'s PR-to-commit SHA matching PLUS
the legacy ``relations/pr_file.py``: this single builder

1. For every PR, walks ``PullRequest.commit_refs`` to resolve each
   :class:`GitHubCommit` (in ``graph.github_commits``),
2. Joins each GitHub commit's ``sha`` to ``graph.commits`` (via the
   :class:`CommitRegistry` primary id — Git commits use the SHA),
3. Walks ``ChangeRegistry.by_commit[commit_ref]`` to find every file
   the commit touched,
4. Emits an :class:`Relation` per (pr, file) pair, strength = number of
   distinct linking commits.

Two windows: ``LIFETIME`` always; ``RECENT`` when host carries a cutoff.
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class PrFileBuilder(RelationBuilder):
    name = "pr.file"
    relation_kind = "pr_file"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        prs = _safe_iter(getattr(graph, "pull_requests", None))
        if not prs:
            return

        cutoff = getattr(graph, "recent_cutoff", None)
        github_commits_get = _entity_by_id(getattr(graph, "github_commits", None))
        commits_get = _entity_by_id(getattr(graph, "commits", None))
        changes_by_commit = _changes_by_commit_index(graph)

        lifetime: dict[tuple[Any, Any], int] = defaultdict(int)
        recent: dict[tuple[Any, Any], int] = defaultdict(int)

        for pr in prs:
            pr_ref = pr.ref()
            commit_refs_on_pr = getattr(pr, "commit_refs", None) or []
            for gh_commit_ref in commit_refs_on_pr:
                gh_commit = github_commits_get(gh_commit_ref.id)
                if gh_commit is None:
                    continue
                sha = getattr(gh_commit, "sha", None)
                if not sha:
                    continue
                # Cross-source join: SHA → git Commit primary id.
                git_commit = commits_get(sha)
                if git_commit is None:
                    continue
                git_commit_ref = git_commit.ref()
                in_recent = cutoff is not None and _commit_in_window(
                    git_commit, cutoff
                )
                seen_files: set[Any] = set()
                for change in changes_by_commit(git_commit_ref):
                    fref = getattr(change, "file_ref", None)
                    if fref is None or fref in seen_files:
                        continue
                    seen_files.add(fref)
                    lifetime[(pr_ref, fref)] += 1
                    if in_recent:
                        recent[(pr_ref, fref)] += 1

        yield from _emit(self.relation_kind, WindowKind.LIFETIME, lifetime)
        if cutoff is not None:
            yield from _emit(self.relation_kind, WindowKind.RECENT, recent)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_iter(reg: Any) -> list[Any]:
    if reg is None:
        return []
    try:
        return list(reg)
    except TypeError:
        return []


def _entity_by_id(reg: Any):
    if reg is None:
        return lambda _id: None
    get = getattr(reg, "get", None)
    if get is None:
        return lambda _id: None
    return get


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


__all__ = ["PrFileBuilder"]
