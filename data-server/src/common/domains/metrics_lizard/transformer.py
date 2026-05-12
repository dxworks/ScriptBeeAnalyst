"""Lizard-metrics-domain :class:`Transformer` implementation.

Per plan §9, every domain ships a transformer that converts a raw mined
payload into a :class:`TransformResult` the processor (Chunk 8) routes into
graph registries.

Two raw input shapes are recognized by
:meth:`LizardMetricsTransformer.transform`:

1. **Already-built entity bundle** — a ``dict`` of the form::

      {
          "project":      LizardMetricsProject(...),
          "file_metrics": Iterable[FileMetric],
      }

   The validation + regrouping is delegated to
   :meth:`Transformer.collect_bundle` (Chunk 5 shared helper).

2. **Raw Lizard CSV** — deferred to Chunk 8 with ``NotImplementedError``.
"""
from __future__ import annotations

from typing import Any, ClassVar, Mapping

from ...kernel import Entity, EntityKind
from ...people import SourceKind
from ..transformer import Transformer, TransformResult
from .models import FileMetric, LizardMetricsProject


_BUCKET_SPECS: dict[str, tuple[EntityKind, type[Entity]]] = {
    "file_metrics": (EntityKind.FILE_METRIC, FileMetric),
}


class LizardMetricsTransformer(Transformer):
    """Concrete :class:`Transformer` for the ``lizard`` source."""

    source: ClassVar[SourceKind] = SourceKind.LIZARD

    def transform(self, raw: Any) -> TransformResult:
        """Convert ``raw`` to a :class:`TransformResult`. See module docs."""
        if isinstance(raw, Mapping):
            return self.collect_bundle(raw, LizardMetricsProject, _BUCKET_SPECS)
        raise NotImplementedError(
            "LizardMetricsTransformer.transform(raw) accepts only an "
            "entity-bundle Mapping today (see module docstring). The "
            "raw-DTO path (Lizard CSV) will be wired in Chunk 8 alongside "
            "the processor rewrite."
        )


__all__ = ["LizardMetricsTransformer"]
