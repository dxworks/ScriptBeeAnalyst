"""Overview-table implementations.

Importing this package side-loads every implementation module, which in
turn registers its :class:`OverviewTableBuilder` with
:data:`src.enrichment.overviews.OVERVIEWS`. All ten implementations are
stubs that raise :class:`NotImplementedError` in :meth:`build` — they
exist so Chunk 8 can introspect the catalog without forcing a full port
now. See the Chunk-7 handoff §"Components and overviews".
"""
from __future__ import annotations

from . import (  # noqa: F401
    authorship_table,
    code_quality_table,
    components_table,
    feature_encapsulation_table,
    feature_traceability_table,
    intent_impact_table,
    knowledge_table,
    nature_table,
    pace_table,
    pr_lifecycle_table,
    testing_table,
)


__all__: list[str] = []
