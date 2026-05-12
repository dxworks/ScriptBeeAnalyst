"""Metric implementations.

Importing this package side-loads every implementation module, which in turn
auto-registers each :class:`Metric` with the module-level
:data:`src.enrichment.metrics.METRICS` singleton via the ``@METRICS.register``
decorator.

See the Chunk-7 handoff for the legacy-file → new-class mapping table.
"""
from __future__ import annotations

# Anomaly metrics — all stubs (NotImplementedError) for this chunk.
from . import (  # noqa: F401
    anomaly_cohesion,
    anomaly_complexity,
    anomaly_coupling,
    anomaly_knowledge,
    anomaly_quality_issues,
    anomaly_structuring,
    anomaly_testing,
    anomaly_timezone,
)

# Classifier metrics — substantively ported.
from . import (  # noqa: F401
    author_classifiers,
    commit_classifiers,
    file_classifiers,
    issue_pr_classifiers,
    pr_traits,
)

# Component resolver — substantively ported (emits component_membership relations).
from . import component_resolver  # noqa: F401


__all__: list[str] = []
