"""File ↔ File aggregate coupling builder.

Port of legacy ``src/enrichment/relations/coupling.py``. Source: every
:class:`CodeReference` regardless of ``reference_kind``. Strength = raw
sum of typed reference weights. Self-loops dropped. ``extras['breakdown']``
carries a per-kind dict so the agent can explain what dominates the
coupling (e.g. ``{"call": 12, "inheritance": 2}``).
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS
from .calls import _entity_by_id, _resolve_file_ref

if TYPE_CHECKING:
    from src.common.kernel import Graph


@BUILDERS.register
class CouplingBuilder(RelationBuilder):
    name = "coupling"
    relation_kind = "coupling"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        code_refs = getattr(graph, "code_refs", None)
        if code_refs is None:
            return

        methods_get = _entity_by_id(getattr(graph, "code_methods", None))
        types_get = _entity_by_id(getattr(graph, "code_types", None))
        fields_get = _entity_by_id(getattr(graph, "code_fields", None))

        # (src_file, tgt_file) -> reference_kind -> summed weight
        pairs: dict[tuple[Any, Any], dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )

        try:
            ref_iter = list(code_refs)
        except TypeError:
            return

        for ref in ref_iter:
            rk = getattr(ref, "reference_kind", None)
            if rk is None:
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
            if src_file is None or tgt_file is None or src_file == tgt_file:
                continue
            weight = int(getattr(ref, "weight", 1) or 0)
            pairs[(src_file, tgt_file)][rk] += weight

        for (src, tgt), breakdown in pairs.items():
            strength = sum(breakdown.values())
            rid = Relation.canonical_id(
                src, tgt, self.relation_kind, WindowKind.LIFETIME
            )
            yield Relation(
                id=rid,
                source=src,
                target=tgt,
                relation_kind=self.relation_kind,
                window=WindowKind.LIFETIME,
                strength=float(strength),
                extras={"breakdown": dict(breakdown)},
            )


__all__ = ["CouplingBuilder"]
