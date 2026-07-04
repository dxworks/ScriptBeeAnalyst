"""Duplication-domain :class:`Transformer` implementation.

Per plan §9, every domain ships a transformer that converts a raw mined
payload into a :class:`TransformResult` the processor (Chunk 8) routes into
graph registries.

Two raw input shapes are recognized by :meth:`DuplicationTransformer.transform`:

1. **Already-built entity bundle** — a ``dict`` of the form::

      {
          "project":          DuplicationProject(...),
          "duplication_pairs": Iterable[DuplicationPair],
      }

   The validation + regrouping is delegated to
   :meth:`Transformer.collect_bundle` (Chunk 5 shared helper).

2. **Raw DuDe CSV / JSON** — deferred to Chunk 8 with
   ``NotImplementedError``.
"""
from __future__ import annotations

from typing import Any, ClassVar, Mapping

from ...kernel import Entity, EntityKind
from ...people.source import SourceKind
from ..transformer import Transformer, TransformResult
from .models import DuplicationPair, DuplicationProject


_BUCKET_SPECS: dict[str, tuple[EntityKind, type[Entity]]] = {
    "duplication_pairs": (EntityKind.DUPLICATION_PAIR, DuplicationPair),
}


class DuplicationTransformer(Transformer):
    """Concrete :class:`Transformer` for the ``duplication`` source."""

    source: ClassVar[SourceKind] = SourceKind.DUPLICATION

    def transform(self, raw: Any) -> TransformResult:
        """Convert ``raw`` to a :class:`TransformResult`. See module docs."""
        if isinstance(raw, Mapping):
            return self.collect_bundle(raw, DuplicationProject, _BUCKET_SPECS)
        raise NotImplementedError(
            "DuplicationTransformer.transform(raw) accepts only an "
            "entity-bundle Mapping today (see module docstring). The "
            "raw-DTO path (DuDe CSV/JSON) will be wired in Chunk 8 "
            "alongside the processor rewrite."
        )


__all__ = ["DuplicationTransformer"]
