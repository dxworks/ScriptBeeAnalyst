"""App-Inspector domain :class:`Transformer` implementation.

Per plan §9, every domain ships a transformer that converts a raw mined
payload into a :class:`TransformResult` the processor (Chunk 8) routes into
graph registries.

Two raw input shapes are recognized by :meth:`AppInspectorTransformer.transform`:

1. **Already-built entity bundle** — a ``dict`` of the form::

      {
          "project":  AppInspectorProject(..., source_tool="appinspector"),
          "app_tags": Iterable[AppTag],
      }

   The validation + regrouping is delegated to
   :meth:`Transformer.collect_bundle` (Chunk 5 shared helper).

2. **Raw App-Inspector / Chronos payload** — deferred to Chunk 8 with
   ``NotImplementedError``. Mirroring the
   :class:`~src.common.domains.quality.transformer.QualityTransformer`
   design, a single transformer handles every shipped source_tool variant
   on the entity-bundle path; the raw-DTO branch lives in Chunk 8.
"""
from __future__ import annotations

from typing import Any, ClassVar, Mapping

from ...kernel import Entity, EntityKind
from ...people import SourceKind
from ..transformer import Transformer, TransformResult
from .models import AppInspectorProject, AppTag


_BUCKET_SPECS: dict[str, tuple[EntityKind, type[Entity]]] = {
    "app_tags": (EntityKind.APP_TAG, AppTag),
}


class AppInspectorTransformer(Transformer):
    """Concrete :class:`Transformer` for the ``app_inspector`` source.

    Today only the entity-bundle path is wired — the raw-DTO branch is
    deferred to Chunk 8 alongside the processor rewrite. The bundle path
    is tool-agnostic; future ``source_tool`` values (e.g. ``"chronos"``)
    will reuse it untouched.
    """

    source: ClassVar[SourceKind] = SourceKind.APP_INSPECTOR

    def transform(self, raw: Any) -> TransformResult:
        """Convert ``raw`` to a :class:`TransformResult`. See module docs."""
        if isinstance(raw, Mapping):
            return self.collect_bundle(raw, AppInspectorProject, _BUCKET_SPECS)
        raise NotImplementedError(
            "AppInspectorTransformer.transform(raw) accepts only an "
            "entity-bundle Mapping today (see module docstring). The "
            "raw-DTO path will be wired in Chunk 8 alongside the "
            "processor rewrite."
        )


__all__ = ["AppInspectorTransformer"]
