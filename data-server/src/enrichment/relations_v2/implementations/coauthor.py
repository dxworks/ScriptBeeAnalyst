"""Co-author builder — author ↔ author edges weighted by shared files.

Port of legacy ``src/enrichment/relations/coauthor.py``. Strength = number
of files both authors changed. Symmetric — each pair emitted once with
``source.id <= target.id`` so the relation list is half-size.

Two windows are emitted per pair when both apply:

* :pyattr:`WindowKind.LIFETIME` — every co-touch in history
* :pyattr:`WindowKind.RECENT`   — co-touches with ``author_date >=
  cutoff``, where ``cutoff`` comes from the v2 host's recent-cutoff
  helper (Chunk-8 will wire one; we degrade gracefully when absent).

Reads from the host: ``files``, ``changes`` (via ``by_file`` index),
``commits``. Tolerates a missing index gracefully.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import TYPE_CHECKING, Any, Iterable

from src.enrichment.relations_v2 import Relation, RelationBuilder, WindowKind
from src.enrichment.relations_v2.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class CoauthorBuilder(RelationBuilder):
    """Author ↔ Author co-authorship — strength = shared file count.

    Emits two :class:`Relation` rows per author-pair when both windows
    apply (lifetime + recent). The relation_kind is the same; the
    ``window`` field discriminates.
    """

    name = "coauthor"
    relation_kind = "coauthor"
    window = WindowKind.LIFETIME  # default — actual emission may add RECENT too

    def build(self, graph: "Graph") -> Iterable[Relation]:
        files = _safe_iter(getattr(graph, "files", None))
        if not files:
            return

        cutoff = getattr(graph, "recent_cutoff", None)

        # Per-file author sets, lifetime + recent.
        lifetime_authors_per_file: dict[Any, set[Any]] = defaultdict(set)
        recent_authors_per_file: dict[Any, set[Any]] = defaultdict(set)

        changes_index = _changes_by_file_index(graph)
        commits_by_id = _entity_by_id(getattr(graph, "commits", None))

        for file_ in files:
            file_ref = file_.ref()
            for change in changes_index(file_ref):
                commit = commits_by_id(change.commit_ref.id)
                if commit is None:
                    continue
                author_ref = commit.author_ref
                if author_ref is None:
                    continue
                lifetime_authors_per_file[file_ref].add(author_ref)
                if cutoff is not None and _commit_in_window(commit, cutoff):
                    recent_authors_per_file[file_ref].add(author_ref)

        yield from _emit_pairs(
            self.relation_kind, WindowKind.LIFETIME, lifetime_authors_per_file
        )
        if cutoff is not None:
            yield from _emit_pairs(
                self.relation_kind, WindowKind.RECENT, recent_authors_per_file
            )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_iter(reg: Any) -> list[Any]:
    """Iterate a registry-like object safely (returns ``[]`` for ``None``)."""
    if reg is None:
        return []
    try:
        return list(reg)
    except TypeError:
        return []


def _changes_by_file_index(graph: Any):
    """Return a callable mapping ``file_ref -> Iterable[Change]``.

    Prefers the v2 ``ChangeRegistry.by_file`` index when present; falls
    back to a full-scan when not.
    """
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _file_ref: []
    by_file = getattr(changes, "by_file", None)
    if by_file is not None:
        return lambda file_ref: by_file[file_ref]

    # Fallback: brute scan once per file. Only used when an index is missing.
    def scan(file_ref):
        return [ch for ch in changes if ch.file_ref == file_ref]

    return scan


def _entity_by_id(reg: Any):
    """Return a callable ``id -> Entity | None`` against a registry."""
    if reg is None:
        return lambda _id: None
    get = getattr(reg, "get", None)
    if get is None:
        return lambda _id: None
    return get


def _commit_in_window(commit: Any, cutoff: Any) -> bool:
    """``True`` iff the commit's ``author_date`` is at or after ``cutoff``."""
    d = getattr(commit, "author_date", None) or getattr(commit, "committer_date", None)
    if d is None or cutoff is None:
        return False
    try:
        return d >= cutoff
    except TypeError:
        return False


def _emit_pairs(
    relation_kind: str,
    window: WindowKind,
    authors_per_file: dict[Any, set[Any]],
) -> Iterable[Relation]:
    """Convert per-file author sets into pairwise :class:`Relation` rows."""
    pair_counts: dict[tuple[Any, Any], int] = defaultdict(int)
    for authors in authors_per_file.values():
        if len(authors) < 2:
            continue
        # Sort by entity id so the pair is canonical (a<=b) — keeps the
        # dedup deterministic.
        ordered = sorted(authors, key=lambda r: (r.kind, r.id))
        for a, b in combinations(ordered, 2):
            pair_counts[(a, b)] += 1

    for (src, tgt), count in pair_counts.items():
        rid = Relation.canonical_id(src, tgt, relation_kind, window)
        yield Relation(
            id=rid,
            source=src,
            target=tgt,
            relation_kind=relation_kind,
            window=window,
            strength=float(count),
        )


__all__ = ["CoauthorBuilder"]
