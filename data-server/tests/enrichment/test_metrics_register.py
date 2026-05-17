"""Every metric implementation registers with :data:`METRICS`.

Mirror of :mod:`test_builders_register`. Importing the implementations
package side-loads every module which decorates its :class:`Metric`
subclass with ``@METRICS.register``.
"""
from __future__ import annotations

import src.enrichment.metrics.implementations  # noqa: F401 — side-effect import
from src.enrichment.metrics import METRICS, Metric, MetricRegistry


_EXPECTED_NAMES = {
    # Substantively-ported metrics (Chunks 7, 11, 12).
    "file.classifiers",
    "commit.classifiers",
    "commit_task_prefixes",       # Chunk 11
    "component.resolver",
    "author.classifiers",         # Chunk 12
    "anomaly.complexity",         # Chunk 12
    "anomaly.coupling",           # Chunk 12
    "anomaly.quality_issues",     # Chunk 12
    "anomaly.cohesion",           # Chunk 15a (coordination + size; activity → 15b)
    "anomaly.structuring",        # Chunk 15
    "anomaly.testing",            # Chunk 15
    # Deferred-stub metrics — flip as Chunks 15b / 16 land.
    "anomaly.knowledge",
    "anomaly.timezone",
    "issue_pr.classifiers",
    "pr.traits",
}


def test_metric_registry_singleton_is_a_metric_registry() -> None:
    assert isinstance(METRICS, MetricRegistry)


def test_every_metric_implementation_registers() -> None:
    names = set(METRICS.names())
    assert _EXPECTED_NAMES.issubset(names), (
        f"Missing: {_EXPECTED_NAMES - names}"
    )


def test_every_registered_class_subclasses_metric() -> None:
    for cls in METRICS:
        assert issubclass(cls, Metric), cls


def test_metric_registry_catalog_size_at_least_15() -> None:
    """One :class:`Metric` per legacy tagger module + the component
    resolver + the Chunk-11 task-prefix classifier.

    8 anomaly_*.py + 4 *_classifiers.py + 1 pr_traits.py + 1 component
    resolver + 1 commit_task_prefixes = 15 total. Later chunks may add more.
    """
    assert len(METRICS) >= 15
