"""Code-structure-domain v2 entities, registries, and transformer.

See plan §3 (``CodeStructureProject.kind_of_source: "jafax" | "codeframe"``)
and §4 (source-domain entities). The public surface here is intentionally
narrow: every concrete class downstream code (Chunks 7/8 + the MCP sandbox)
needs is re-exported.
"""
from __future__ import annotations

from .models import (
    CodeField,
    CodeMethod,
    CodeReference,
    CodeStructureProject,
    CodeType,
    KindOfSource,
)
from .registries import (
    CodeFieldRegistry,
    CodeMethodRegistry,
    CodeReferenceRegistry,
    CodeStructureProjectRegistry,
    CodeTypeRegistry,
)


# Lazy transformer export — see Chunk 8 note in the git/__init__.py twin.
def __getattr__(name):  # PEP 562
    if name == "CodeStructureTransformer":
        from .transformer import CodeStructureTransformer

        return CodeStructureTransformer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # models
    "CodeField",
    "CodeMethod",
    "CodeReference",
    "CodeStructureProject",
    "CodeType",
    "KindOfSource",
    # registries
    "CodeFieldRegistry",
    "CodeMethodRegistry",
    "CodeReferenceRegistry",
    "CodeStructureProjectRegistry",
    "CodeTypeRegistry",
    # transformer
    "CodeStructureTransformer",
]
