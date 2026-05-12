"""Issue ↔ Issue builder.

Port of legacy ``src/enrichment/relations/issue_issue.py``. Two
contributions to strength per (issue_a, issue_b) pair:

* native parent/child links — weight 2;
* shared-file overlap — weight 1 per shared file.

The v2 ``Issue`` model carries ``parent_ref`` directly. The
``children`` side comes from ``IssueRegistry.by_parent[issue.ref()]``.
Shared files are read by following each issue's
``issue_file`` :class:`Relation` rows in ``graph.relations`` — i.e.
**this builder must run AFTER :class:`IssueFileBuilder`**. The pipeline
preserves builder registration order; both are decorated in
``relations_v2/implementations/__init__.py`` with issue_file first.

``extras['native_link']`` distinguishes structural vs. derived edges.
Lifetime only — issue links are static.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import TYPE_CHECKING, Any, Iterable

from src.enrichment.relations_v2 import Relation, RelationBuilder, WindowKind
from src.enrichment.relations_v2.builders import BUILDERS

_NATIVE_WEIGHT = 2.0

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class IssueIssueBuilder(RelationBuilder):
    name = "issue.issue"
    relation_kind = "issue_issue"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        issues = _safe_iter(getattr(graph, "issues", None))
        if not issues:
            return

        # Native parent/child edges.
        native: set[tuple[Any, Any]] = set()
        for issue in issues:
            issue_ref = issue.ref()
            parent_ref = getattr(issue, "parent_ref", None)
            if parent_ref is not None:
                pair = _canonical_pair(issue_ref, parent_ref)
                native.add(pair)

            # Children via the by_parent index when available.
            by_parent = _by_parent_index(graph)
            for child in by_parent(issue_ref):
                child_ref = child.ref()
                pair = _canonical_pair(issue_ref, child_ref)
                native.add(pair)

        # Shared-file overlap: read each issue's file-touch set from the
        # already-populated ``issue_file`` relations in ``graph.relations``.
        files_by_issue: dict[Any, set[Any]] = defaultdict(set)
        for rel in _iter_issue_file_relations(graph):
            files_by_issue[rel.source].add(rel.target)

        shared_pairs: dict[tuple[Any, Any], int] = defaultdict(int)
        issue_refs = sorted(
            files_by_issue.keys(), key=lambda r: (r.kind, r.id)
        )
        for a, b in combinations(issue_refs, 2):
            overlap = len(files_by_issue[a] & files_by_issue[b])
            if overlap > 0:
                pair = _canonical_pair(a, b)
                shared_pairs[pair] = overlap

        all_pairs: set[tuple[Any, Any]] = set(native) | set(shared_pairs.keys())
        for pair in all_pairs:
            src, tgt = pair
            shared = shared_pairs.get(pair, 0)
            is_native = pair in native
            strength = (_NATIVE_WEIGHT if is_native else 0.0) + float(shared)
            if strength <= 0:
                continue
            rid = Relation.canonical_id(
                src, tgt, "issue_issue", WindowKind.LIFETIME
            )
            yield Relation(
                id=rid,
                source=src,
                target=tgt,
                relation_kind="issue_issue",
                window=WindowKind.LIFETIME,
                strength=strength,
                extras={"native_link": is_native, "shared_files": shared},
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


def _canonical_pair(a: Any, b: Any) -> tuple[Any, Any]:
    """Sort a pair by (kind, id) so (a, b) and (b, a) match the same id."""
    if (a.kind, a.id) <= (b.kind, b.id):
        return (a, b)
    return (b, a)


def _by_parent_index(graph: Any):
    issues = getattr(graph, "issues", None)
    if issues is None:
        return lambda _parent_ref: []
    by_parent = getattr(issues, "by_parent", None)
    if by_parent is not None:
        return lambda parent_ref: by_parent[parent_ref]

    def scan(parent_ref):
        return [
            issue
            for issue in issues
            if getattr(issue, "parent_ref", None) == parent_ref
        ]

    return scan


def _iter_issue_file_relations(graph: Any) -> Iterable[Any]:
    relations = getattr(graph, "relations", None)
    if relations is None:
        return ()
    of_kind = getattr(relations, "of_kind", None)
    if of_kind is not None:
        return of_kind("issue_file")
    # Fallback for hosts without the index — full scan.
    try:
        return [r for r in relations if r.relation_kind == "issue_file"]
    except TypeError:
        return ()


__all__ = ["IssueIssueBuilder"]
