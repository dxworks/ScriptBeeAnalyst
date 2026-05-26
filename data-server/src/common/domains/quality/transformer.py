"""Quality-domain :class:`Transformer` implementation.

Per plan §9, every domain ships a transformer that converts a raw mined
payload into a :class:`TransformResult` the processor (Chunk 8) routes into
graph registries.

Two raw input shapes are recognized by :meth:`QualityTransformer.transform`:

1. **Already-built entity bundle** — a ``dict`` of the form::

      {
          "project":         QualityProject(..., source_tool="insider"),
          "quality_issues":  Iterable[QualityIssue],
      }

   The validation + regrouping is delegated to
   :meth:`Transformer.collect_bundle` (Chunk 5 shared helper).

2. **Raw Insider / Sonar payload** — deferred to Chunk 8 with
   ``NotImplementedError``. Plan §9 mentions Sonar replacing Insider;
   mirroring the code-structure design, a single transformer handles
   both formats and Chunk 8 branches on ``project.source_tool`` to pick
   the right raw-DTO walker.
"""
from __future__ import annotations

from typing import Any, ClassVar, Mapping

from ...kernel import Entity, EntityKind
from ...people.source import SourceKind
from ..transformer import Transformer, TransformResult
from .models import QualityIssue, QualityProject


_BUCKET_SPECS: dict[str, tuple[EntityKind, type[Entity]]] = {
    "quality_issues": (EntityKind.QUALITY_ISSUE, QualityIssue),
}


class QualityTransformer(Transformer):
    """Concrete :class:`Transformer` for the ``quality`` source.

    Branches on ``project.source_tool`` (Insider vs Sonar) only for the
    raw-DTO path — Chunk 8 plumbs the two builders. The entity-bundle
    path is tool-agnostic.
    """

    source: ClassVar[SourceKind] = SourceKind.QUALITY

    def transform(self, raw: Any) -> TransformResult:
        """Convert ``raw`` to a :class:`TransformResult`. See module docs."""
        if isinstance(raw, Mapping):
            return self.collect_bundle(raw, QualityProject, _BUCKET_SPECS)
        raise NotImplementedError(
            "QualityTransformer.transform(raw) accepts only an "
            "entity-bundle Mapping today (see module docstring). The "
            "raw-DTO path (branching on project.source_tool for Insider "
            "vs Sonar) will be wired in Chunk 8 alongside the processor "
            "rewrite."
        )


__all__ = ["QualityTransformer"]
