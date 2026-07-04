"""Code-structure-domain :class:`Transformer` implementation.

Per plan §9, every domain ships a transformer that converts a raw mined
payload into a :class:`TransformResult` the processor (Chunk 8) routes into
graph registries.

Two raw input shapes are recognized by :meth:`CodeStructureTransformer.transform`:

1. **Already-built entity bundle** — a ``dict`` of the form::

      {
          "project":     CodeStructureProject(..., kind_of_source="codeframe"),
          "code_types":  Iterable[CodeType],
          "code_methods": Iterable[CodeMethod],
          "code_fields": Iterable[CodeField],
          "code_refs":   Iterable[CodeReference],
      }

   The validation + regrouping is delegated to
   :meth:`Transformer.collect_bundle` (Chunk 5 shared helper). This
   transformer only declares the bucket specs.

2. **Raw Codeframe DTO** — not consumed directly here. The
   ``code_structure`` bundle is produced upstream by
   :mod:`src.common.domains.code_structure.bridge`, which streams a
   CodeFrame ``.jsonl`` file and returns the entity bundle the processor
   then feeds back through path (1).

The transformer stays format-agnostic — the only source-specific code
lives in the bridge.
"""
from __future__ import annotations

from typing import Any, ClassVar, Mapping

from ...kernel import Entity, EntityKind
from ...people.source import SourceKind
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

    Format-agnostic — the raw-DTO walking lives in
    :mod:`src.common.domains.code_structure.bridge`; this transformer
    only ingests the already-built entity bundle.
    """

    source: ClassVar[SourceKind] = SourceKind.CODE_STRUCTURE

    def transform(self, raw: Any) -> TransformResult:
        """Convert ``raw`` to a :class:`TransformResult`. See module docs."""
        if isinstance(raw, Mapping):
            return self.collect_bundle(raw, CodeStructureProject, _BUCKET_SPECS)
        raise NotImplementedError(
            "CodeStructureTransformer.transform(raw) accepts only an "
            "entity-bundle Mapping (see module docstring). Raw CodeFrame "
            "JSONL is parsed upstream by "
            "src.common.domains.code_structure.bridge.build_code_structure_bundle."
        )


__all__ = ["CodeStructureTransformer"]
