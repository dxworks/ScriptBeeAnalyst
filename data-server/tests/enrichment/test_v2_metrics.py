"""Chunk-3 tests for :mod:`src.enrichment.metrics`.

Covers:

* :class:`Metric` is an ABC — direct instantiation and ``compute``-less
  subclasses must fail.
* :class:`MetricInputs` / :class:`MetricOutputs` are typed Pydantic models
  (no ``Any``, ``extra="forbid"``, frozen).
* :class:`MetricRegistry` register / get / iter / contains.
* Decorator-style registration works (``@METRICS.register``).
* Re-registering the same class is a no-op; a different class under the
  same name raises.
* Registering a non-Metric / class without ``name`` raises.
* The module-level :data:`METRICS` singleton is the shared catalog.

Test filename uses the ``test_v2_`` prefix to keep pytest module ids
consistent with the chunk-3 sibling tests for tags / relations (which
collide with legacy names in the same directory).
"""
from __future__ import annotations

from typing import Any, ClassVar, Iterable

import pytest
from pydantic import ValidationError

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.metrics import (
    METRICS,
    Metric,
    MetricInputs,
    MetricOutput,
    MetricOutputs,
    MetricRegistry,
)
from src.enrichment.relations_v2 import Relation, WindowKind
from src.enrichment.tags import Trait, TraitFamily


# ---------------------------------------------------------------------------
# Helpers — a minimal concrete Metric we can register / instantiate.
# ---------------------------------------------------------------------------
class _OrphanMetric(Metric):
    """Stand-in for the legacy ``OrphanTagger`` Chunk 7 will port."""

    name: ClassVar[str] = "chunk3.test.orphan"
    inputs: ClassVar[MetricInputs] = MetricInputs(
        source_kind=EntityKind.FILE,
    )
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=["anomaly.knowledge.Orphan"],
    )
    config_fields: ClassVar[list[str]] = ["orphan_inactive_days"]

    def compute(self, graph: Any, config: Any) -> Iterable[MetricOutput]:
        # Trivial output for the test — yields one Trait so the
        # union return-type is exercised at runtime.
        yield Trait(
            id="t-orphan-1",
            target=EntityRef(kind=EntityKind.FILE, id="a.py"),
            family=TraitFamily.KNOWLEDGE,
            name="anomaly.knowledge.Orphan",
            severity=1.0,
        )


class _RelationMetric(Metric):
    """A second concrete metric to exercise registry uniqueness."""

    name: ClassVar[str] = "chunk3.test.relation"
    inputs: ClassVar[MetricInputs] = MetricInputs(relation_kind="cochange")
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_relations=["cochange.scored"],
    )

    def compute(self, graph: Any, config: Any) -> Iterable[MetricOutput]:
        src = EntityRef(kind=EntityKind.FILE, id="a")
        tgt = EntityRef(kind=EntityKind.FILE, id="b")
        yield Relation(
            id=Relation.canonical_id(src, tgt, "cochange.scored"),
            source=src,
            target=tgt,
            relation_kind="cochange.scored",
            window=WindowKind.LIFETIME,
            strength=0.5,
        )


# ---------------------------------------------------------------------------
# Metric ABC
# ---------------------------------------------------------------------------
def test_metric_is_abstract_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        Metric()  # type: ignore[abstract]


def test_metric_subclass_missing_compute_is_abstract() -> None:
    class _Incomplete(Metric):
        name: ClassVar[str] = "x"
        inputs: ClassVar[MetricInputs] = MetricInputs()
        outputs: ClassVar[MetricOutputs] = MetricOutputs()

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_metric_subclass_with_compute_instantiates() -> None:
    inst = _OrphanMetric()
    out = list(inst.compute(graph=None, config=None))
    assert len(out) == 1
    assert isinstance(out[0], Trait)


# ---------------------------------------------------------------------------
# MetricInputs / MetricOutputs — typed, frozen, no Any
# ---------------------------------------------------------------------------
def test_metric_inputs_defaults_are_none() -> None:
    mi = MetricInputs()
    assert mi.relation_kind is None
    assert mi.source_kind is None
    assert mi.target_kind is None


def test_metric_inputs_accepts_entity_kind_values() -> None:
    mi = MetricInputs(source_kind=EntityKind.FILE,
                       target_kind=EntityKind.COMMIT,
                       relation_kind="cochange")
    assert mi.source_kind == EntityKind.FILE
    assert mi.target_kind == EntityKind.COMMIT
    assert mi.relation_kind == "cochange"


def test_metric_outputs_default_lists_empty() -> None:
    mo = MetricOutputs()
    assert mo.emits_traits == []
    assert mo.emits_classifiers == []
    assert mo.emits_relations == []
    assert mo.emits_overview_columns == []


def test_metric_inputs_outputs_are_frozen() -> None:
    """Plan §7 calls these ``ClassVar``s — they must be immutable so two
    instances of the same metric can't drift apart."""
    mi = MetricInputs(source_kind=EntityKind.FILE)
    with pytest.raises(ValidationError):
        mi.source_kind = EntityKind.COMMIT  # type: ignore[misc]

    mo = MetricOutputs(emits_traits=["x"])
    with pytest.raises(ValidationError):
        mo.emits_traits = ["y"]  # type: ignore[misc]


def test_metric_inputs_forbids_extras() -> None:
    with pytest.raises(ValidationError):
        MetricInputs(unknown_field="oops")  # type: ignore[call-arg]


def test_metric_outputs_forbids_extras() -> None:
    with pytest.raises(ValidationError):
        MetricOutputs(unknown_field="oops")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# MetricRegistry — register / get / iter / contains
# ---------------------------------------------------------------------------
def test_metric_registry_register_and_get() -> None:
    reg = MetricRegistry()
    reg.register(_OrphanMetric)
    assert reg.get("chunk3.test.orphan") is _OrphanMetric


def test_metric_registry_iteration() -> None:
    reg = MetricRegistry()
    reg.register(_OrphanMetric)
    reg.register(_RelationMetric)
    assert set(reg) == {_OrphanMetric, _RelationMetric}
    assert reg.all() == [_OrphanMetric, _RelationMetric]
    assert reg.names() == ["chunk3.test.orphan", "chunk3.test.relation"]
    assert len(reg) == 2


def test_metric_registry_contains_by_name() -> None:
    reg = MetricRegistry()
    reg.register(_OrphanMetric)
    assert "chunk3.test.orphan" in reg
    assert "missing" not in reg


def test_metric_registry_get_missing_raises_key_error() -> None:
    reg = MetricRegistry()
    with pytest.raises(KeyError):
        reg.get("nope")


def test_metric_registry_clear() -> None:
    reg = MetricRegistry()
    reg.register(_OrphanMetric)
    reg.clear()
    assert len(reg) == 0


# ---------------------------------------------------------------------------
# Decorator-style registration
# ---------------------------------------------------------------------------
def test_metric_registry_works_as_decorator() -> None:
    reg = MetricRegistry()

    @reg.register
    class _DecoMetric(Metric):
        name: ClassVar[str] = "chunk3.test.deco"
        inputs: ClassVar[MetricInputs] = MetricInputs()
        outputs: ClassVar[MetricOutputs] = MetricOutputs()

        def compute(self, graph: Any, config: Any) -> Iterable[MetricOutput]:
            return iter([])

    assert reg.get("chunk3.test.deco") is _DecoMetric


# ---------------------------------------------------------------------------
# Registration error paths
# ---------------------------------------------------------------------------
def test_metric_registry_reregister_same_class_is_noop() -> None:
    reg = MetricRegistry()
    reg.register(_OrphanMetric)
    reg.register(_OrphanMetric)  # re-import simulation
    assert len(reg) == 1


def test_metric_registry_collision_with_different_class_raises() -> None:
    reg = MetricRegistry()
    reg.register(_OrphanMetric)

    class _Impersonator(Metric):
        name: ClassVar[str] = "chunk3.test.orphan"  # same name
        inputs: ClassVar[MetricInputs] = MetricInputs()
        outputs: ClassVar[MetricOutputs] = MetricOutputs()

        def compute(self, graph: Any, config: Any) -> Iterable[MetricOutput]:
            return iter([])

    with pytest.raises(ValueError):
        reg.register(_Impersonator)


def test_metric_registry_rejects_non_metric_class() -> None:
    reg = MetricRegistry()

    class _NotAMetric:
        name = "x"

    with pytest.raises(TypeError):
        reg.register(_NotAMetric)  # type: ignore[arg-type]


def test_metric_registry_rejects_metric_without_name() -> None:
    reg = MetricRegistry()

    class _NamelessMetric(Metric):
        # No ``name`` ClassVar — accessing ``name`` on the class raises
        # AttributeError, so registration must reject it.
        inputs: ClassVar[MetricInputs] = MetricInputs()
        outputs: ClassVar[MetricOutputs] = MetricOutputs()

        def compute(self, graph: Any, config: Any) -> Iterable[MetricOutput]:
            return iter([])

    with pytest.raises(TypeError):
        reg.register(_NamelessMetric)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
def test_module_level_singleton_is_a_metric_registry() -> None:
    assert isinstance(METRICS, MetricRegistry)


def test_module_level_singleton_round_trip_then_clean_up() -> None:
    """Demonstrates the decorator-on-singleton flow Chunk 7 will use.

    Cleans up after itself so the global catalog doesn't carry test
    metrics into other test files.
    """
    assert "chunk3.test.singleton" not in METRICS

    @METRICS.register
    class _SingletonProbe(Metric):
        name: ClassVar[str] = "chunk3.test.singleton"
        inputs: ClassVar[MetricInputs] = MetricInputs()
        outputs: ClassVar[MetricOutputs] = MetricOutputs()

        def compute(self, graph: Any, config: Any) -> Iterable[MetricOutput]:
            return iter([])

    try:
        assert METRICS.get("chunk3.test.singleton") is _SingletonProbe
        assert "chunk3.test.singleton" in METRICS
    finally:
        # Drop the test entry so we don't pollute the global catalog.
        # ``unregister`` is the public test-isolation primitive (review #5).
        METRICS.unregister("chunk3.test.singleton")


def test_metric_registry_unregister_returns_class_and_removes_it() -> None:
    """``unregister(name)`` is the public counterpart to ``register(cls)``;
    it returns the dropped class and is the preferred test-isolation
    primitive (vs. poking at ``_by_name``)."""
    reg = MetricRegistry()
    reg.register(_OrphanMetric)
    assert _OrphanMetric.name in reg

    removed = reg.unregister(_OrphanMetric.name)
    assert removed is _OrphanMetric
    assert _OrphanMetric.name not in reg


def test_metric_registry_unregister_unknown_name_raises_key_error() -> None:
    reg = MetricRegistry()
    with pytest.raises(KeyError):
        reg.unregister("does.not.exist")
