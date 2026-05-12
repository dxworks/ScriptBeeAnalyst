"""Cochange (file ↔ file) builder.

Port of legacy ``src/enrichment/relations/cochange.py`` (the central
file-file cochange edge). Strength = number of commits that touched
both files. Two emissions per pair: ``LIFETIME`` and ``RECENT``.

Skips:

* merge commits (``len(parent_refs) > 1``);
* bulk commits (more than ``cochange_max_files_per_commit`` files) —
  defaults to 20 from :class:`EnrichmentConfig`. Bulk commits drown out
  real coupling signal.

Reads from the host: ``commits`` (whole registry), ``changes`` (via the
``by_commit`` index). Tolerates missing indexes gracefully.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import TYPE_CHECKING, Any, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


_DEFAULT_MAX_FILES_PER_COMMIT = 20


@BUILDERS.register
class CochangeBuilder(RelationBuilder):
    """File ↔ File co-change — strength = number of co-touching commits.

    Aggregates pairs (a, b) with ``a.id < b.id`` so the edge list is
    half-size. Two windows emitted per pair (lifetime always; recent
    when at least one co-touching commit is in the window).
    """

    name = "cochange"
    relation_kind = "cochange"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        commits = _safe_iter(getattr(graph, "commits", None))
        if not commits:
            return

        cutoff = getattr(graph, "recent_cutoff", None)
        max_files = _config_field(
            graph, "cochange_max_files_per_commit", _DEFAULT_MAX_FILES_PER_COMMIT
        )

        changes_by_commit = _changes_by_commit_index(graph)

        lifetime: dict[tuple[Any, Any], int] = defaultdict(int)
        recent: dict[tuple[Any, Any], int] = defaultdict(int)

        for commit in commits:
            # Skip merges.
            parents = getattr(commit, "parent_refs", None) or []
            if len(parents) > 1:
                continue

            # File refs touched by this commit.
            changes = list(changes_by_commit(commit.ref()))
            if not (2 <= len(changes) <= max_files):
                continue

            file_refs: list[Any] = []
            for ch in changes:
                fref = getattr(ch, "file_ref", None)
                if fref is not None:
                    file_refs.append(fref)
            unique = sorted(set(file_refs), key=lambda r: (r.kind, r.id))
            if len(unique) < 2:
                continue

            in_recent = cutoff is not None and _commit_in_window(commit, cutoff)
            for a, b in combinations(unique, 2):
                lifetime[(a, b)] += 1
                if in_recent:
                    recent[(a, b)] += 1

        yield from _emit_pairs(self.relation_kind, WindowKind.LIFETIME, lifetime)
        if cutoff is not None:
            yield from _emit_pairs(self.relation_kind, WindowKind.RECENT, recent)


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


def _changes_by_commit_index(graph: Any):
    """Return a callable ``commit_ref -> Iterable[Change]``."""
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


def _config_field(graph: Any, field: str, default: Any) -> Any:
    """Resolve ``field`` from a config attached to ``graph`` or fall back."""
    cfg = getattr(graph, "config", None)
    if cfg is None:
        return default
    return getattr(cfg, field, default)


def _emit_pairs(
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


__all__ = ["CochangeBuilder"]
