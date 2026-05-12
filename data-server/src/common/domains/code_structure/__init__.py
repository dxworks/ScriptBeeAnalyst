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
from .transformer import CodeStructureTransformer

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
