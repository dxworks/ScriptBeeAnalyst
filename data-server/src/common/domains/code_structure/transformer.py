"""Code-structure-domain :class:`Transformer` implementation.

Per plan §9, every domain ships a transformer that converts a raw mined
payload into a :class:`TransformResult` the processor (Chunk 8) routes into
graph registries.

Two raw input shapes are recognized by :meth:`CodeStructureTransformer.transform`:

1. **Already-built entity bundle** — a ``dict`` of the form::

      {
          "project":     CodeStructureProject(..., kind_of_source="jafax"),
          "code_types":  Iterable[CodeType],
          "code_methods": Iterable[CodeMethod],
          "code_fields": Iterable[CodeField],
          "code_refs":   Iterable[CodeReference],
      }

   The validation + regrouping is delegated to
   :meth:`Transformer.collect_bundle` (Chunk 5 shared helper). This
   transformer only declares the bucket specs.

2. **Raw JaFax / Codeframe DTO** — deferred to Chunk 8 with
   ``NotImplementedError``. The plan §3 says ``kind_of_source`` lives on
   the project, so a single transformer handles both formats; Chunk 8
   inspects ``project.kind_of_source`` to choose which raw-DTO walker
   (``JaFaxTransformer`` rewrite or the future ``CodeframeTransformer``)
   builds the bundle.

Per the Chunk-6 brief, branching on ``kind_of_source`` is acceptable for
the single-transformer design. Task 8 (the JaFax→Codeframe flip) only
needs a new builder for the bundle, not a new transformer class.
"""
from __future__ import annotations

from typing import Any, ClassVar, Mapping

from ...kernel import Entity, EntityKind
from ...people import SourceKind
from ..transformer import Transformer, TransformResult
from .models import (
    CodeField,
    CodeMethod,
    CodeReference,
    CodeStructureProject,
    CodeType,
)


_BUCKET_SPECS: dict[str, tuple[EntityKind, type[Entity]]] = {
    "code_types": (EntityKind.CODE_TYPE, CodeType),
    "code_methods": (EntityKind.CODE_METHOD, CodeMethod),
    "code_fields": (EntityKind.CODE_FIELD, CodeField),
    "code_refs": (EntityKind.CODE_REF, CodeReference),
}


class CodeStructureTransformer(Transformer):
    """Concrete :class:`Transformer` for the ``code_structure`` source.

    Branches on ``project.kind_of_source`` (JaFax vs Codeframe) only for
    the raw-DTO path — Chunk 8 plumbs the two builders. The entity-bundle
    path is format-agnostic.
    """

    source: ClassVar[SourceKind] = SourceKind.CODE_STRUCTURE

    def transform(self, raw: Any) -> TransformResult:
        """Convert ``raw`` to a :class:`TransformResult`. See module docs."""
        if isinstance(raw, Mapping):
            return self.collect_bundle(raw, CodeStructureProject, _BUCKET_SPECS)
        raise NotImplementedError(
            "CodeStructureTransformer.transform(raw) accepts only an "
            "entity-bundle Mapping today (see module docstring). The "
            "raw-DTO path (branching on project.kind_of_source for "
            "JaFax vs Codeframe) will be wired in Chunk 8 alongside the "
            "processor rewrite."
        )


__all__ = ["CodeStructureTransformer"]
