"""Metric implementations.

Importing this package side-loads every implementation module, which in turn
auto-registers each :class:`Metric` with the module-level
:data:`src.enrichment.metrics.METRICS` singleton via the ``@METRICS.register``
decorator.

See the Chunk-7 handoff for the legacy-file → new-class mapping table.
"""
from __future__ import annotations

# Classifier / resolver metrics — must run BEFORE anomaly metrics
# because several anomaly metrics (anomaly_testing, future Chunk-16
# anomaly_knowledge) read pre-computed classifiers via
# ``graph.classifiers.with_value(...)``. The pipeline iterates
# ``METRICS`` in registration order; registration order = import
# order here. Putting classifiers first guarantees stage-2 read-
# after-write within a single ``run_pipeline`` call.
from . import (  # noqa: F401
    author_classifiers,
    commit_classifiers,
    commit_task_prefixes,
    file_classifiers,
    issue_pr_classifiers,
    pr_traits,
)

# Component resolver — substantively ported (emits component_membership relations).
from . import component_resolver  # noqa: F401

# Anomaly metrics — read upstream classifiers via the registry.
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


__all__: list[str] = []
