"""MetricRegistry — plain catalog of :class:`Metric` subclasses.

Distinct from ``src.common.kernel.Registry`` because a :class:`Metric` is
**not** a graph entity (no :class:`EntityKind`, no ``id`` field — it's
code). The :class:`Registry`-base requires ``T: bound=Entity`` and would
force an artificial ``EntityKind.METRIC`` member just to satisfy the type.

See §7 of ``architectural_changes.md`` and the Chunk-3 handoff.

Usage::

    from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs

    @METRICS.register
    class KnowledgeOrphan(Metric):
        name = "knowledge.orphan"
        inputs = MetricInputs(source_kind=EntityKind.FILE)
        outputs = MetricOutputs(emits_traits=["anomaly.knowledge.Orphan"])
        def compute(self, graph, config): ...

    # Anywhere later:
    cls = METRICS.get("knowledge.orphan")    # type[Metric]
    instance = cls()
    for emitted in instance.compute(graph, config):
        ...

    for cls in METRICS:                        # iterator over type[Metric]
        ...

The module-level singleton :data:`METRICS` is the canonical catalog. Tests
that need an isolated registry can instantiate :class:`MetricRegistry`
directly.
"""
from __future__ import annotations

from typing import Iterator

from .base import Metric


class MetricRegistry:
    """Catalog of :class:`Metric` subclasses, keyed by ``Metric.name``.

    Decorator-friendly: ``MetricRegistry.register`` returns the class so
    it can be used as ``@METRICS.register``.

    Not picklable — the catalog is a global, code-only artefact that gets
    rebuilt on every import. Storage layer (§8 of the plan) does not see
    metrics.
    """

    __slots__ = ("_by_name",)

    def __init__(self) -> None:
        self._by_name: dict[str, type[Metric]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(self, metric_cls: type[Metric]) -> type[Metric]:
        """Add ``metric_cls`` to the catalog. Returns it (decorator-style).

        Raises :class:`ValueError` if a different class is already
        registered under the same ``name``. Re-registering the *same*
        class (e.g. due to a module re-import) is a no-op.
        """
        if not isinstance(metric_cls, type):  # defensive — caller error
            raise TypeError(
                f"MetricRegistry.register expects a class, got "
                f"{type(metric_cls).__name__}"
            )
        if not issubclass(metric_cls, Metric):
            raise TypeError(
                f"{metric_cls.__name__} must subclass Metric to register"
            )
        name = getattr(metric_cls, "name", None)
        if not isinstance(name, str) or not name:
            raise TypeError(
                f"{metric_cls.__name__} must declare a non-empty "
                f"``name: ClassVar[str]`` to register"
            )
        existing = self._by_name.get(name)
        if existing is not None and existing is not metric_cls:
            raise ValueError(
                f"MetricRegistry already has a different class registered "
                f"under name {name!r}: {existing.__name__} (new: "
                f"{metric_cls.__name__})"
            )
        self._by_name[name] = metric_cls
        return metric_cls

    # ------------------------------------------------------------------
    # Lookup / iteration
    # ------------------------------------------------------------------
    def get(self, name: str) -> type[Metric]:
        """Return the metric class registered under ``name``.

        Raises :class:`KeyError` if not found.
        """
        try:
            return self._by_name[name]
        except KeyError as e:
            raise KeyError(
                f"No metric registered under name {name!r}. Registered: "
                f"{sorted(self._by_name)}"
            ) from e

    def all(self) -> list[type[Metric]]:
        """Snapshot list of every registered metric class."""
        return list(self._by_name.values())

    def names(self) -> list[str]:
        """Snapshot list of registered metric names (insertion order)."""
        return list(self._by_name.keys())

    def __iter__(self) -> Iterator[type[Metric]]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    # ------------------------------------------------------------------
    # Test / cleanup
    # ------------------------------------------------------------------
    def unregister(self, name: str) -> type[Metric]:
        """Drop the metric registered under ``name`` and return its class.

        Public test-isolation primitive — preferred over poking at
        ``_by_name`` directly. Raises :class:`KeyError` if no metric
        is registered under ``name``.
        """
        try:
            return self._by_name.pop(name)
        except KeyError as e:
            raise KeyError(
                f"No metric registered under name {name!r}. Registered: "
                f"{sorted(self._by_name)}"
            ) from e

    def clear(self) -> None:
        """Drop every registered metric. Mainly for test isolation."""
        self._by_name.clear()


#: The module-level singleton. Chunk 7 ports each tagger/builder to a
#: ``Metric`` subclass and decorates with ``@METRICS.register``.
METRICS = MetricRegistry()


__all__ = ["METRICS", "MetricRegistry"]
