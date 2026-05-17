"""File-name similarity builder — v2 port.

Port of legacy ``src/enrichment/relations/similarity_file_names.py``. The
legacy computes ``difflib.SequenceMatcher`` ratios over file basenames
with extension-bucket pre-filter + per-file top-N cap. Lifetime only —
file paths have no recent semantic.

v2 differences vs. legacy:

* Walks ``graph.files`` (typed registry) instead of
  ``git.file_registry.all``.
* Emits :class:`Relation` (typed entity, dedup-by-canonical-id) instead
  of legacy ``Relation`` dataclasses on a ``RelationFile`` envelope.
* Reads thresholds (``name_similarity_min_score``,
  ``name_similarity_extension_filter``,
  ``name_similarity_max_pairs_per_file``) from ``config`` if it carries
  them; falls back to sensible defaults otherwise so a bare-bones host
  / minimal-config call still produces output.

Bounding the O(N²) cost (same scheme as the legacy):

* ``name_similarity_extension_filter=True`` restricts comparisons to
  same-extension pairs (cheap pre-filter).
* ``name_similarity_max_pairs_per_file`` keeps only the top-N ratios
  per source file. An edge survives only if BOTH endpoints kept it
  (symmetric cap), matching the legacy "no half-edges" rule.
"""
from __future__ import annotations

import os
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any, Iterable

from src.common.kernel import EntityRef
from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


# Legacy defaults from ``EnrichmentConfig`` (kept inline as fallbacks so
# tests that pass a bare ``object()`` config still get sane behaviour).
_DEFAULT_MIN_SCORE: float = 0.85
_DEFAULT_EXTENSION_FILTER: bool = False
_DEFAULT_MAX_PAIRS_PER_FILE: int = 50


@BUILDERS.register
class SimilarityFileNamesBuilder(RelationBuilder):
    """Emits ``similarity.file-file.names`` lifetime relations.

    Strength is the ``difflib.SequenceMatcher`` ratio over file
    basenames. See module docstring for the bounding scheme.
    """

    name = "similarity.file_names"
    relation_kind = "similarity.file-file.names"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        files = _safe_iter(getattr(graph, "files", None))
        if not files:
            return

        # Configuration knobs — fall back to legacy defaults when the
        # config isn't attached to the host. Builders don't receive
        # ``config`` directly (see :class:`RelationBuilder.build`); the
        # convention across v2 builders (cf. cochange.py) is to read off
        # ``graph.config`` when present, else use sane defaults. The
        # production Graph carries no ``config`` attribute today, so the
        # defaults apply unless a test stub host is used.
        cfg = getattr(graph, "config", None)
        min_score: float = _config_field(cfg, "name_similarity_min_score", _DEFAULT_MIN_SCORE)
        ext_filter: bool = _config_field(cfg, "name_similarity_extension_filter", _DEFAULT_EXTENSION_FILTER)
        cap: int = _config_field(cfg, "name_similarity_max_pairs_per_file", _DEFAULT_MAX_PAIRS_PER_FILE)

        # Snapshot of (path, EntityRef) pairs. Sort by path so the
        # comparison order is deterministic — tie-breaks in the cap
        # become reproducible across pickle cycles.
        entries: list[tuple[str, EntityRef]] = []
        for f in files:
            path = getattr(f, "path", None) or getattr(f, "id", None)
            if not isinstance(path, str) or not path:
                continue
            ref = f.ref() if hasattr(f, "ref") else None
            if ref is None:
                continue
            entries.append((path, ref))
        entries.sort(key=lambda kv: kv[0])

        if len(entries) < 2:
            return

        # Bucket by extension if filtering, else single bucket.
        buckets: dict[str, list[tuple[str, EntityRef]]] = {}
        for path, ref in entries:
            base = os.path.basename(path)
            ext = os.path.splitext(base)[1].lower() if ext_filter else ""
            buckets.setdefault(ext, []).append((path, ref))

        # Per-file priority cap — keep top-cap edges per ref (sorted desc).
        per_file_top: dict[EntityRef, list[tuple[float, EntityRef]]] = {}

        def _consider(src: EntityRef, dst: EntityRef, score: float) -> None:
            arr = per_file_top.setdefault(src, [])
            arr.append((score, dst))
            if len(arr) > cap:
                arr.sort(key=lambda kv: -kv[0])
                del arr[cap:]

        for bucket in buckets.values():
            n = len(bucket)
            for i in range(n):
                a_path, a_ref = bucket[i]
                base_a = os.path.basename(a_path)
                for j in range(i + 1, n):
                    b_path, b_ref = bucket[j]
                    base_b = os.path.basename(b_path)
                    score = SequenceMatcher(None, base_a, base_b).ratio()
                    if score < min_score:
                        continue
                    _consider(a_ref, b_ref, score)
                    _consider(b_ref, a_ref, score)

        # Symmetric cap — an edge survives only if BOTH endpoints kept
        # it. Emit each unordered pair once with canonical id.
        emitted: set[tuple[EntityRef, EntityRef]] = set()
        for src_ref, arr in per_file_top.items():
            for score, dst_ref in arr:
                pair = _ordered_pair(src_ref, dst_ref)
                if pair in emitted:
                    continue
                # Check the other endpoint also kept this edge.
                other_arr = per_file_top.get(dst_ref, [])
                if not any(o is src_ref or o == src_ref for _, o in other_arr):
                    continue
                emitted.add(pair)
                a_ref, b_ref = pair
                yield Relation(
                    id=Relation.canonical_id(
                        a_ref, b_ref, self.relation_kind, WindowKind.LIFETIME
                    ),
                    source=a_ref,
                    target=b_ref,
                    relation_kind=self.relation_kind,
                    window=WindowKind.LIFETIME,
                    strength=round(float(score), 4),
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


def _config_field(cfg: Any, field: str, default: Any) -> Any:
    """Read ``field`` from a config-like object; fall back to ``default``."""
    if cfg is None:
        return default
    return getattr(cfg, field, default)


def _ordered_pair(a: EntityRef, b: EntityRef) -> tuple[EntityRef, EntityRef]:
    """Deterministic ordering for (a, b) so the canonical id is stable."""
    if (a.kind.value, a.id) <= (b.kind.value, b.id):
        return (a, b)
    return (b, a)


__all__ = ["SimilarityFileNamesBuilder"]
