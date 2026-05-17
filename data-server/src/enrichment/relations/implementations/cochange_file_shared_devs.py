"""Cochange (file ↔ file, shared-devs) builder — Chunk 13 port.

Emits one edge per ordered file pair ``(a, b)`` whose distinct-author
sets overlap by at least ``shared_devs_min`` developers (default 1 —
mirroring the legacy permissive emission).

Algorithm
---------

Per the plan §2 Chunk-13 reuse map:

    "Reuse the Chunk-7 ``ownership`` relations for per-file author
     overlap — no new structure needed."

So this builder consumes the already-aggregated v2
:class:`OwnershipBuilder` output (``relation_kind="ownership"``) rather
than re-walking ``graph.changes`` and ``graph.commits``. Concretely:

1. Read ``graph.relations.of_kind("ownership")`` filtered to
   ``WindowKind.LIFETIME`` (recent ownership relations are handled by
   the RECENT-window emission below).
2. Group ``ownership.source`` (author refs) by ``ownership.target``
   (file refs) → ``dict[file_ref, set[author_ref]]``.
3. For every unordered file pair, intersect the author sets; if
   ``|shared| >= shared_devs_min`` emit a relation with strength
   ``|shared|``.

Edge gating: per the legacy ``cochange.file-file.shared-devs``, an edge
is only emitted for file pairs that *actually co-changed in a single
commit*. We adopt the same gate via
``graph.relations.of_kind("cochange")`` so this builder is a true
filtering / re-weighting of the central cochange edge — not a fresh
"any two files with any shared author" emission. Without the gate, two
files Alice touched on entirely separate days would emit an edge even
though they never appeared in the same commit; that contradicts the
"co-change" framing.

Reads
-----

* ``graph.relations.of_kind("ownership")`` — per-file author sets.
* ``graph.relations.of_kind("cochange")``  — co-change gate.
* No direct ``commits`` / ``changes`` reads (Chunk-13 single-source rule).
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import EntityRef, Graph


_DEFAULT_SHARED_DEVS_MIN = 1


@BUILDERS.register
class CochangeFileSharedDevsBuilder(RelationBuilder):
    """Filter cochange edges by shared-developer overlap.

    Two emissions per pair: ``LIFETIME`` (uses lifetime ownership +
    lifetime cochange); ``RECENT`` (uses recent ownership + recent
    cochange) when ``graph.recent_cutoff`` is set.
    """

    name = "cochange.file_shared_devs"
    relation_kind = "cochange_file_shared_devs"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        relations = getattr(graph, "relations", None)
        if relations is None:
            return

        cutoff = getattr(graph, "recent_cutoff", None)
        min_shared = _config_field(
            graph, "shared_devs_min", _DEFAULT_SHARED_DEVS_MIN
        )

        # LIFETIME emission.
        yield from _emit_for_window(
            relations,
            WindowKind.LIFETIME,
            self.relation_kind,
            min_shared,
        )
        # RECENT emission only when a recent cutoff is in play (mirrors
        # the upstream OwnershipBuilder / CochangeBuilder contract).
        if cutoff is not None:
            yield from _emit_for_window(
                relations,
                WindowKind.RECENT,
                self.relation_kind,
                min_shared,
            )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _emit_for_window(
    relations: Any,
    window: WindowKind,
    relation_kind: str,
    min_shared: int,
) -> Iterable[Relation]:
    authors_by_file = _authors_by_file(relations, window)
    if not authors_by_file:
        return
    cochange_pairs = _cochange_pairs(relations, window)
    if not cochange_pairs:
        return
    for pair in cochange_pairs:
        a, b = pair
        shared = authors_by_file.get(a, frozenset()) & authors_by_file.get(b, frozenset())
        if len(shared) < min_shared:
            continue
        ordered = _ordered_pair(a, b)
        rid = Relation.canonical_id(*ordered, relation_kind, window)
        yield Relation(
            id=rid,
            source=ordered[0],
            target=ordered[1],
            relation_kind=relation_kind,
            window=window,
            strength=float(len(shared)),
            extras={
                "shared_devs": int(len(shared)),
                "shared_author_ids": sorted(a.id for a in shared),
            },
        )


def _authors_by_file(relations: Any, window: WindowKind) -> dict["EntityRef", frozenset["EntityRef"]]:
    """Map ``file_ref → set(author_ref)`` from ``ownership`` relations."""
    lookup = getattr(relations, "of_kind_in_window", None)
    if callable(lookup):
        try:
            edges = lookup("ownership", window)
        except (KeyError, ValueError):
            edges = ()
    else:
        edges = _filter_kind_window(relations, "ownership", window)

    if not edges:
        return {}

    bag: dict[Any, set[Any]] = defaultdict(set)
    for rel in edges:
        # Ownership: source=author_ref, target=file_ref.
        bag[rel.target].add(rel.source)
    return {file_ref: frozenset(authors) for file_ref, authors in bag.items()}


def _cochange_pairs(relations: Any, window: WindowKind) -> set[tuple["EntityRef", "EntityRef"]]:
    """Set of unordered file pairs that co-changed in the same window."""
    lookup = getattr(relations, "of_kind_in_window", None)
    if callable(lookup):
        try:
            edges = lookup("cochange", window)
        except (KeyError, ValueError):
            edges = ()
    else:
        edges = _filter_kind_window(relations, "cochange", window)

    pairs: set[tuple[Any, Any]] = set()
    for rel in edges:
        pairs.add(_ordered_pair(rel.source, rel.target))
    return pairs


def _filter_kind_window(
    relations: Any, relation_kind: str, window: WindowKind
) -> list[Any]:
    """Fallback when ``of_kind_in_window`` is unavailable (test stub)."""
    out: list[Any] = []
    try:
        iterable = list(relations)
    except TypeError:
        return out
    for rel in iterable:
        if getattr(rel, "relation_kind", None) != relation_kind:
            continue
        if getattr(rel, "window", None) != window:
            continue
        out.append(rel)
    return out


def _config_field(graph: Any, field: str, default: Any) -> Any:
    cfg = getattr(graph, "config", None)
    if cfg is None:
        return default
    return getattr(cfg, field, default)


def _ordered_pair(a: "EntityRef", b: "EntityRef") -> tuple["EntityRef", "EntityRef"]:
    """Canonical ordering by ``(kind, id)`` so (a,b) and (b,a) collapse."""
    key_a = (a.kind, a.id)
    key_b = (b.kind, b.id)
    return (a, b) if key_a <= key_b else (b, a)


__all__ = ["CochangeFileSharedDevsBuilder"]
