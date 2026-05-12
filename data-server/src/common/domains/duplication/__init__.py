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
from .transformer import DuplicationTransformer

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
