"""Chunk-7 pipeline driver tests.

Synthetic mini-host + a fake builder + a fake metric. Verifies:

* Counts in :class:`PipelineResult` (traits/classifiers/relations).
* ``builders_run`` / ``metrics_run`` lists.
* Per-step failures appear in ``errors`` and don't abort the pipeline.
* The pipeline accepts a stub host (no full :class:`Graph` needed).
* Empty registries (``builders=[]``) skip a stage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Iterable

import pytest

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.metrics import Metric, MetricInputs, MetricOutputs
from src.enrichment.relations import (
    Relation,
    RelationBuilder,
    RelationRegistry,
    WindowKind,
)
from src.enrichment.tags import (
    Classifier,
    ClassifierRegistry,
    Trait,
    TraitFamily,
    TraitRegistry,
)
from src.enrichment.pipeline import (
    PipelineError,
    PipelineResult,
    run_pipeline,
)


@dataclass
class _StubHost:
    """Minimal :class:`PipelineHost`-shaped host for tests."""

    relations: RelationRegistry
    traits: TraitRegistry
    classifiers: ClassifierRegistry


@pytest.fixture
def host() -> _StubHost:
    return _StubHost(
        relations=RelationRegistry(),
        traits=TraitRegistry(),
        classifiers=ClassifierRegistry(),
    )


# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------
class _FakeBuilder(RelationBuilder):
    """Yields two ``cochange`` relations between two synthetic files."""

    name: ClassVar[str] = "test.fake_builder"
    relation_kind: ClassVar[str] = "cochange"
    window: ClassVar[WindowKind] = WindowKind.LIFETIME

    def build(self, graph: Any) -> Iterable[Relation]:
        a = EntityRef(kind=EntityKind.FILE, id="a.py")
        b = EntityRef(kind=EntityKind.FILE, id="b.py")
        c = EntityRef(kind=EntityKind.FILE, id="c.py")
        for src, tgt in ((a, b), (a, c)):
            yield Relation(
                id=Relation.canonical_id(src, tgt, "cochange"),
                source=src,
                target=tgt,
                relation_kind="cochange",
                window=WindowKind.LIFETIME,
                strength=1.0,
            )


class _FakeMetric(Metric):
    """Emits one trait + one classifier + one relation."""

    name: ClassVar[str] = "test.fake_metric"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=["FakeTrait"],
        emits_classifiers=["fake_dim"],
        emits_relations=["fake_relation"],
    )
    config_fields: ClassVar[list[str]] = []

    def compute(self, graph: Any, config: Any) -> Iterable[Any]:
        target = EntityRef(kind=EntityKind.FILE, id="a.py")
        yield Trait(
            id="FakeTrait:file/a.py",
            target=target,
            family=TraitFamily.KNOWLEDGE,
            name="FakeTrait",
        )
        yield Classifier(
            id="fake_dim:file/a.py",
            target=target,
            dimension="fake_dim",
            value="x",
        )
        other = EntityRef(kind=EntityKind.FILE, id="b.py")
        yield Relation(
            id=Relation.canonical_id(target, other, "fake_relation"),
            source=target,
            target=other,
            relation_kind="fake_relation",
            strength=0.5,
        )


class _FailingBuilder(RelationBuilder):
    name: ClassVar[str] = "test.failing_builder"
    relation_kind: ClassVar[str] = "bad"

    def build(self, graph: Any) -> Iterable[Relation]:
        raise RuntimeError("synthetic builder failure")


class _FailingMetric(Metric):
    name: ClassVar[str] = "test.failing_metric"
    inputs: ClassVar[MetricInputs] = MetricInputs()
    outputs: ClassVar[MetricOutputs] = MetricOutputs()

    def compute(self, graph: Any, config: Any) -> Iterable[Any]:
        raise RuntimeError("synthetic metric failure")


class _UnsupportedOutputMetric(Metric):
    """Yields a non-Trait/Classifier/Relation object — triggers TypeError."""

    name: ClassVar[str] = "test.unsupported_metric"
    inputs: ClassVar[MetricInputs] = MetricInputs()
    outputs: ClassVar[MetricOutputs] = MetricOutputs()

    def compute(self, graph: Any, config: Any) -> Iterable[Any]:
        yield {"not": "an entity"}  # type: ignore[misc]


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
def test_pipeline_runs_fake_builder_and_fake_metric(host: _StubHost) -> None:
    result = run_pipeline(
        host,
        config=None,
        builders=[_FakeBuilder],
        metrics=[_FakeMetric],
    )

    assert isinstance(result, PipelineResult)
    assert result.builders_run == ["test.fake_builder"]
    assert result.metrics_run == ["test.fake_metric"]

    # Builder yields 2 cochange relations; metric yields 1 fake_relation + 1 trait + 1 classifier.
    assert result.relations_emitted == 3
    assert result.traits_emitted == 1
    assert result.classifiers_emitted == 1
    assert result.errors == []

    # Verify the registries actually received them.
    assert len(host.relations) == 3
    assert len(host.traits) == 1
    assert len(host.classifiers) == 1


def test_pipeline_records_builder_failure_but_continues(host: _StubHost) -> None:
    result = run_pipeline(
        host,
        config=None,
        builders=[_FailingBuilder, _FakeBuilder],
        metrics=[],
    )
    assert result.builders_run == ["test.fake_builder"]
    assert len(result.errors) == 1
    err = result.errors[0]
    assert isinstance(err, PipelineError)
    assert err.step == "builder"
    assert err.name == "test.failing_builder"
    assert err.error_type == "RuntimeError"
    assert "synthetic builder failure" in err.message
    # The fake builder STILL ran (2 relations emitted).
    assert result.relations_emitted == 2


def test_pipeline_records_metric_failure_but_continues(host: _StubHost) -> None:
    result = run_pipeline(
        host,
        config=None,
        builders=[],
        metrics=[_FailingMetric, _FakeMetric],
    )
    assert result.metrics_run == ["test.fake_metric"]
    assert len(result.errors) == 1
    assert result.errors[0].step == "metric"
    assert result.errors[0].name == "test.failing_metric"
    assert result.traits_emitted == 1
    assert result.classifiers_emitted == 1
    assert result.relations_emitted == 1


def test_pipeline_routes_unsupported_metric_output_to_errors(
    host: _StubHost,
) -> None:
    result = run_pipeline(
        host,
        config=None,
        builders=[],
        metrics=[_UnsupportedOutputMetric],
    )
    assert result.metrics_run == []
    assert len(result.errors) == 1
    assert result.errors[0].error_type == "TypeError"
    assert "unsupported" in result.errors[0].message.lower()


def test_pipeline_skips_stage_when_iterable_empty(host: _StubHost) -> None:
    result = run_pipeline(host, config=None, builders=[], metrics=[])
    assert result.builders_run == []
    assert result.metrics_run == []
    assert result.traits_emitted == 0
    assert result.classifiers_emitted == 0
    assert result.relations_emitted == 0
    assert result.errors == []


def test_pipeline_result_is_pydantic_dumpable(host: _StubHost) -> None:
    """:class:`PipelineResult` must round-trip cleanly through Pydantic."""
    result = run_pipeline(
        host, config=None, builders=[_FakeBuilder], metrics=[_FakeMetric]
    )
    dumped = result.model_dump()
    assert dumped["traits_emitted"] == 1
    assert dumped["classifiers_emitted"] == 1
    assert dumped["relations_emitted"] == 3
    # Round-trip back into a model.
    rebuilt = PipelineResult.model_validate(dumped)
    assert rebuilt == result


def test_pipeline_defaults_to_global_catalogs(host: _StubHost) -> None:
    """Omitting ``builders``/``metrics`` reads :data:`BUILDERS` / :data:`METRICS`.

    Importing the implementation packages registers ~24 builders + ~14
    metrics; running the pipeline against the empty host should
    therefore touch a non-trivial number of names. Every deferred stub
    raises :class:`NotImplementedError` which is captured in ``errors``
    rather than aborting — verified here.
    """
    # Side-effect imports — register every implementation.
    import src.enrichment.relations.implementations  # noqa: F401
    import src.enrichment.metrics.implementations  # noqa: F401

    result = run_pipeline(host, config=None)

    # We don't pin the exact counts (chunks 8+ may add more) but we
    # demand the catalogs aren't empty.
    assert len(result.builders_run) + len(result.errors) >= 10
    # Stubs raise NotImplementedError; the pipeline records them. The
    # empty host means substantively-ported builders also produce
    # zero relations, but they don't ERROR (they short-circuit on the
    # missing registry).
    deferred_errors = [
        e for e in result.errors if e.error_type == "NotImplementedError"
    ]
    assert len(deferred_errors) >= 10  # the 9 cochange stubs + similarity, etc.
