"""File ↔ File method-call builder.

Port of legacy ``src/enrichment/relations/calls.py``. Source: every
:class:`CodeReference` whose ``reference_kind == "call"``. Strength =
sum of ``weight`` across matching refs. Self-loops dropped — a file
calling itself carries no inter-file coupling signal.

Reads from the host: ``code_refs`` (the v2
:class:`CodeReferenceRegistry`). Resolves each reference's source/target
to a file ref by following the ``source_method_ref``/``source_type_ref``
+ ``target_method_ref``/``target_type_ref`` chain through the
``code_methods`` / ``code_types`` / ``code_fields`` registries to pick
up ``file_ref``.
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class CallsBuilder(RelationBuilder):
    name = "calls"
    relation_kind = "calls"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        yield from _emit_typed_refs(
            graph,
            self.relation_kind,
            allowed_reference_kinds={"call"},
        )


def _emit_typed_refs(
    graph: Any,
    relation_kind: str,
    allowed_reference_kinds: set[str],
) -> Iterable[Relation]:
    """Generic helper used by :mod:`calls`, :mod:`hierarchy`, :mod:`data_access`.

    Walks ``graph.code_refs``, resolves each ref's source/target to a
    file ref through ``code_methods`` / ``code_types`` / ``code_fields``,
    aggregates by (source_file, target_file), and emits a single
    :class:`Relation` per ordered file pair with ``strength = sum(weight)``.
    Self-loops are dropped.
    """
    code_refs = getattr(graph, "code_refs", None)
    if code_refs is None:
        return
    methods_get = _entity_by_id(getattr(graph, "code_methods", None))
    types_get = _entity_by_id(getattr(graph, "code_types", None))
    fields_get = _entity_by_id(getattr(graph, "code_fields", None))

    pairs: dict[tuple[Any, Any], int] = defaultdict(int)

    try:
        ref_iter = list(code_refs)
    except TypeError:
        return

    for ref in ref_iter:
        rk = getattr(ref, "reference_kind", None)
        if rk not in allowed_reference_kinds:
            continue
        src_file = _resolve_file_ref(
            ref,
            ("source_method_ref", methods_get),
            ("source_type_ref", types_get),
        )
        tgt_file = _resolve_file_ref(
            ref,
            ("target_method_ref", methods_get),
            ("target_type_ref", types_get),
            ("target_field_ref", fields_get),
        )
        if src_file is None or tgt_file is None:
            continue
        if src_file == tgt_file:
            continue
        weight = int(getattr(ref, "weight", 1) or 0)
        pairs[(src_file, tgt_file)] += weight

    for (src, tgt), strength in pairs.items():
        rid = Relation.canonical_id(src, tgt, relation_kind, WindowKind.LIFETIME)
        yield Relation(
            id=rid,
            source=src,
            target=tgt,
            relation_kind=relation_kind,
            window=WindowKind.LIFETIME,
            strength=float(strength),
        )


def _resolve_file_ref(ref: Any, *attr_getters: tuple[str, Any]) -> Any:
    """Walk the (attr_name, registry_get) pairs to find a ``file_ref``."""
    for attr_name, registry_get in attr_getters:
        candidate_ref = getattr(ref, attr_name, None)
        if candidate_ref is None:
            continue
        target = registry_get(candidate_ref.id)
        if target is None:
            continue
        file_ref = getattr(target, "file_ref", None)
        if file_ref is not None:
            return file_ref
    return None


def _entity_by_id(reg: Any):
    if reg is None:
        return lambda _id: None
    get = getattr(reg, "get", None)
    if get is None:
        return lambda _id: None
    return get


__all__ = ["CallsBuilder", "_emit_typed_refs"]
