"""Source-domain packages for the v2 graph.

Each subpackage (``git``, ``jira``, ``github``, …) provides three modules:

* ``models.py``       — entity definitions (subclassing :class:`Entity`)
* ``registries.py``   — typed registries with declared indexes
* ``transformer.py``  — a :class:`Transformer` implementation that turns
                        raw mined data into ``TransformResult`` payloads

The shared :class:`Transformer` ABC + :class:`TransformResult` live in
:mod:`src.common.domains.transformer` (Chunk 4 ships it; Chunks 5 / 6 use
the same contract). See plan §9 (Recipe — adding a new data source).
"""
from __future__ import annotations

from .transformer import Transformer, TransformResult

__all__ = ["Transformer", "TransformResult"]
