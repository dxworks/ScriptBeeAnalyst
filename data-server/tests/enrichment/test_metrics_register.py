"""Every metric implementation registers with :data:`METRICS`.

Mirror of :mod:`test_builders_register`. Importing the implementations
package side-loads every module which decorates its :class:`Metric`
subclass with ``@METRICS.register``.
"""
from __future__ import annotations

import src.enrichment.metrics.implementations  # noqa: F401 — side-effect import
from src.enrichment.metrics import METRICS, Metric, MetricRegistry


_EXPECTED_NAMES = {
    # Substantively-ported metrics.
    "file.classifiers",
    "commit.classifiers",
    "component.resolver",
    # Deferred-stub metrics.
    "anomaly.cohesion",
    "anomaly.complexity",
    "anomaly.coupling",
    "anomaly.knowledge",
    "anomaly.quality_issues",
    "anomaly.structuring",
    "anomaly.testing",
    "anomaly.timezone",
    "author.classifiers",
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


def test_metric_registry_catalog_size_at_least_14() -> None:
    """One :class:`Metric` per legacy tagger module + the component resolver.

    8 anomaly_*.py + 4 *_classifiers.py + 1 pr_traits.py + 1 component
    resolver = 14 total. Chunks 8+ may add more.
    """
    assert len(METRICS) >= 14
