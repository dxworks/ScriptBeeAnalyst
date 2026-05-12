"""Re-export :class:`Trait` and :class:`TraitFamily` from :mod:`.base`.

The plan §13 calls for ``tags/trait.py`` as a dedicated module. We keep the
actual class on :mod:`base` so it can share the ``Tag`` abstract parent and
the recursive ``EvidenceValue`` alias cleanly; this file is the public name
the recipe in §5.3 of the plan points downstream chunks at::

    from src.enrichment.tags.trait import Trait, TraitFamily
"""
from __future__ import annotations

from .base import EvidenceValue, Trait, TraitFamily

__all__ = ["EvidenceValue", "Trait", "TraitFamily"]
