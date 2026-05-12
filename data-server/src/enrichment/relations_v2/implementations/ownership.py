"""Ownership (author → file) builder.

Port of legacy ``src/enrichment/relations/ownership.py``. Strength =
``author_churn / total_file_churn`` (relative ownership). The absolute
churn is preserved in ``extras['absolute_churn']``.

Emits two windows per (author, file) pair: ``LIFETIME`` always; ``RECENT``
when the host carries a recent cutoff.
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, Iterable

from src.enrichment.relations_v2 import Relation, RelationBuilder, WindowKind
from src.enrichment.relations_v2.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class OwnershipBuilder(RelationBuilder):
    name = "ownership"
    relation_kind = "ownership"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        files = _safe_iter(getattr(graph, "files", None))
        if not files:
            return

        cutoff = getattr(graph, "recent_cutoff", None)

        changes_by_file = _changes_by_file_index(graph)
        commits_get = _entity_by_id(getattr(graph, "commits", None))
        hunks_by_change = _hunks_by_change_index(graph)

        lifetime: dict[tuple[Any, Any], int] = defaultdict(int)
        recent: dict[tuple[Any, Any], int] = defaultdict(int)
        lifetime_totals: dict[Any, int] = defaultdict(int)
        recent_totals: dict[Any, int] = defaultdict(int)

        for file_ in files:
            file_ref = file_.ref()
            for change in changes_by_file(file_ref):
                commit = commits_get(change.commit_ref.id)
                if commit is None:
                    continue
                author_ref = getattr(commit, "author_ref", None)
                if author_ref is None:
                    continue
                churn = _change_churn(change, hunks_by_change)
                lifetime[(author_ref, file_ref)] += churn
                lifetime_totals[file_ref] += churn
                if cutoff is not None and _commit_in_window(commit, cutoff):
                    recent[(author_ref, file_ref)] += churn
                    recent_totals[file_ref] += churn

        yield from _emit(
            self.relation_kind, WindowKind.LIFETIME, lifetime, lifetime_totals
        )
        if cutoff is not None:
            yield from _emit(
                self.relation_kind, WindowKind.RECENT, recent, recent_totals
            )


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


def _changes_by_file_index(graph: Any):
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _file_ref: []
    by_file = getattr(changes, "by_file", None)
    if by_file is not None:
        return lambda file_ref: by_file[file_ref]

    def scan(file_ref):
        return [ch for ch in changes if ch.file_ref == file_ref]

    return scan


def _entity_by_id(reg: Any):
    if reg is None:
        return lambda _id: None
    get = getattr(reg, "get", None)
    if get is None:
        return lambda _id: None
    return get


def _hunks_by_change_index(graph: Any):
    hunks = getattr(graph, "hunks", None)
    if hunks is None:
        return lambda _change_ref: []
    by_change = getattr(hunks, "by_change", None)
    if by_change is not None:
        return lambda change_ref: by_change[change_ref]

    def scan(change_ref):
        return [h for h in hunks if h.change_ref == change_ref]

    return scan


def _change_churn(change: Any, hunks_by_change) -> int:
    """Sum of added+deleted lines across all hunks of a change.

    Returns at least 1 so a binary / no-hunk change still records the
    touch (matches legacy ``ownership._change_churn``).
    """
    total = 0
    for hunk in hunks_by_change(change.ref()):
        total += len(getattr(hunk, "added_lines", []) or [])
        total += len(getattr(hunk, "deleted_lines", []) or [])
    return total if total > 0 else 1


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
    totals: dict[Any, int],
) -> Iterable[Relation]:
    for (author_ref, file_ref), churn in pairs.items():
        total = totals.get(file_ref, 0)
        strength = (churn / total) if total > 0 else 0.0
        rid = Relation.canonical_id(
            author_ref, file_ref, relation_kind, window
        )
        yield Relation(
            id=rid,
            source=author_ref,
            target=file_ref,
            relation_kind=relation_kind,
            window=window,
            strength=round(strength, 6),
            extras={
                "absolute_churn": int(churn),
                "file_total_churn": int(total),
            },
        )


__all__ = ["OwnershipBuilder"]
