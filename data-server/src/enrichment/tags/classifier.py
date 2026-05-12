"""Re-export :class:`Classifier` from :mod:`.base`.

The plan §13 calls for ``tags/classifier.py`` as a dedicated module. The
implementation lives in :mod:`base` (sharing the abstract ``Tag`` parent);
this file is the public name downstream chunks import from::

    from src.enrichment.tags.classifier import Classifier
"""
from __future__ import annotations

from .base import Classifier

__all__ = ["Classifier"]
