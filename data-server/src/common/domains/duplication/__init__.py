"""Duplication-domain v2 entities, registries, and transformer.

See plan §4 (source-domain entities) and §9 (transformer contract).
"""
from __future__ import annotations

from .models import (
    DuplicationKind,
    DuplicationPair,
    DuplicationProject,
)
from .registries import (
    DuplicationPairRegistry,
    DuplicationProjectRegistry,
)


# Lazy transformer export — see Chunk 8 note in the git/__init__.py twin.
def __getattr__(name):  # PEP 562
    if name == "DuplicationTransformer":
        from .transformer import DuplicationTransformer

        return DuplicationTransformer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # models
    "DuplicationKind",
    "DuplicationPair",
    "DuplicationProject",
    # registries
    "DuplicationPairRegistry",
    "DuplicationProjectRegistry",
    # transformer
    "DuplicationTransformer",
]
