"""Shared helper for component-domain cochange aggregation — Chunk 14.

The four ``cochange.component*`` builders share a single algorithm:

1. Build a :class:`ComponentResolver` from
   ``config.components_mapping_path`` (or a heuristic-only fallback when
   the config has no path — the same resilient path
   :class:`ComponentResolverMetric` uses).
2. Read a source file-cochange relation kind from
   ``graph.relations.of_kind_in_window(...)``.
3. For each file-cochange edge, resolve both endpoints' file paths to
   component names via the resolver; drop self-loops + un-resolvable
   endpoints.
4. Accumulate per-component-pair strengths and emit one
   :class:`Relation` per pair per window.

Why a private helper module rather than four parallel copies
------------------------------------------------------------

Pure code reuse — the aggregation shape is byte-for-byte the same
across all four component cochange variants (only the source-kind
string and the destination-kind differ). Splitting into four ~120-LOC
copies is the YAGNI-violating outcome the §5 reuse map argues against.

The module is underscore-prefixed and lives next to its consumers so
the public ``BUILDERS`` catalog isn't polluted; nothing outside this
package imports it.

Component resolver caching
--------------------------

Construction is cheap (one regex sort over the mapping) but each
builder runs the resolver once per pipeline pass. We re-resolve every
file_ref in every builder — the per-builder overhead is O(|edges| ×
|mapping|). The resolver is stateless, so a future optimisation could
memoise ``file_ref → component_name`` once at the top of the pipeline
and share across all four builders, but at Zeppelin scale (~5k files,
~6 mapping entries) the cost is negligible — keep it simple.
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, Iterable

from src.common.domains.components.resolver import (
    ComponentResolver,
    load_component_mapping,
    parse_component_mapping,
)
from src.common.kernel import EntityKind, EntityRef
from src.enrichment.relations import Relation, WindowKind

if TYPE_CHECKING:
    from src.common.kernel import Graph


def build_resolver(graph: "Graph") -> ComponentResolver:
    """Construct the :class:`ComponentResolver` the same way
    :class:`ComponentResolverMetric` does — prefer the per-project
    ``components_mapping_data`` dict, fall back to
    ``components_mapping_path`` when no per-project mapping is set, and
    fall back to heuristic mode silently when neither resolves.
    """
    cfg = getattr(graph, "config", None)
    if cfg is None:
        return ComponentResolver(load_component_mapping(None))
    data = getattr(cfg, "components_mapping_data", None)
    if data:
        return ComponentResolver(parse_component_mapping(data))
    path = getattr(cfg, "components_mapping_path", None)
    return ComponentResolver(load_component_mapping(path))


def aggregate_file_relations_to_components(
    graph: "Graph",
    *,
    source_kind: str,
    target_kind: str,
    extras_factory=None,
    min_strength: float = 0.0,
) -> Iterable[Relation]:
    """Aggregate file-* relations into component-* relations.

    Parameters
    ----------
    graph
        The host graph. Must expose ``relations.of_kind_in_window`` (or
        be iterable as the test-stub fallback).
    source_kind
        The file-domain relation kind to read (e.g. ``"cochange"``).
    target_kind
        The component-domain relation kind to emit (e.g.
        ``"cochange_component"``).
    extras_factory
        Optional ``(pair_key, strength) -> dict`` factory for the
        per-relation ``extras``. ``None`` → ``{}``.

    Yields
    ------
    :class:`Relation`
        One per component-pair per emission window. Strengths are summed
        across all file-pairs landing on the same component-pair.
    """
    relations = getattr(graph, "relations", None)
    if relations is None:
        return

    resolver = build_resolver(graph)

    # We aggregate per (window) so LIFETIME and RECENT emissions stay
    # separated. The set of windows worth emitting is exactly the set
    # the source file-cochange builder produced — read both upfront.
    for window in (WindowKind.LIFETIME, WindowKind.RECENT):
        edges = _of_kind_in_window(relations, source_kind, window)
        if not edges:
            continue
        # Skip RECENT emission when the host has no recent cutoff set —
        # the file-* builders only produce RECENT edges when a cutoff is
        # present, so an empty RECENT bucket means "no cutoff". The
        # ``not edges`` guard above already handles that for most
        # cases; this explicit check keeps the contract symmetric with
        # the source builders.
        accum: dict[tuple[str, str], float] = defaultdict(float)
        for rel in edges:
            a_name = resolver.resolve(_extract_file_id(rel.source))
            b_name = resolver.resolve(_extract_file_id(rel.target))
            if a_name is None or b_name is None or a_name == b_name:
                continue
            pair = _ordered_pair_names(a_name, b_name)
            accum[pair] += float(rel.strength)
        for (a_name, b_name), strength in accum.items():
            if strength < min_strength:
                continue
            src_ref = EntityRef(kind=EntityKind.COMPONENT, id=a_name)
            tgt_ref = EntityRef(kind=EntityKind.COMPONENT, id=b_name)
            rid = Relation.canonical_id(src_ref, tgt_ref, target_kind, window)
            extras = (
                extras_factory((a_name, b_name), strength)
                if extras_factory is not None
                else {}
            )
            yield Relation(
                id=rid,
                source=src_ref,
                target=tgt_ref,
                relation_kind=target_kind,
                window=window,
                strength=round(strength, 4),
                extras=extras,
            )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _of_kind_in_window(relations: Any, relation_kind: str, window: WindowKind):
    """Snapshot edges matching ``(relation_kind, window)``.

    Prefers the indexed lookup; falls back to a full scan when the host
    is a test stub without :class:`RelationRegistry`'s indexes.
    """
    lookup = getattr(relations, "of_kind_in_window", None)
    if callable(lookup):
        try:
            return lookup(relation_kind, window)
        except (KeyError, ValueError):
            return ()
    return _filter_kind_window(relations, relation_kind, window)


def _filter_kind_window(
    relations: Any, relation_kind: str, window: WindowKind
) -> list[Any]:
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


def _extract_file_id(ref: "EntityRef") -> str:
    """Return the underlying path-id of a file ref. In v2 ``File.id`` is
    the path itself, so we just hand back ``ref.id``. Centralised so a
    future schema change (e.g. uuid file ids + a side table) only
    touches this one helper.
    """
    return getattr(ref, "id", "") or ""


def _ordered_pair_names(a: str, b: str) -> tuple[str, str]:
    """Canonical lexicographic ordering for component-name pairs."""
    return (a, b) if a <= b else (b, a)


__all__ = [
    "aggregate_file_relations_to_components",
    "build_resolver",
]
