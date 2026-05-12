"""Lizard-metrics-domain entity construction tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.common.domains.metrics_lizard import (
    FileMetric,
    FunctionMetric,
    LizardMetricsProject,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "lz-proj-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)
FILE_REF = EntityRef(kind=EntityKind.FILE, id="src/Foo.java")


def test_lizard_metrics_project_construct():
    p = LizardMetricsProject(
        id=PROJECT_ID, name="ZEPPELIN", source=SourceKind.LIZARD
    )
    assert p.kind == EntityKind.PROJECT
    assert p.source == SourceKind.LIZARD


def test_lizard_metrics_project_transformer_class():
    from src.common.domains.metrics_lizard.transformer import (
        LizardMetricsTransformer,
    )

    p = LizardMetricsProject(
        id=PROJECT_ID, name="z", source=SourceKind.LIZARD
    )
    assert p.transformer_class() is LizardMetricsTransformer


def test_lizard_metrics_project_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        LizardMetricsProject(
            id=PROJECT_ID,
            name="z",
            source=SourceKind.LIZARD,
            mystery=1,  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# FileMetric (one entity per (file, metric_name) pair)
# ---------------------------------------------------------------------------


def test_file_metric_construct_scalar_row():
    m = FileMetric(
        id=FileMetric.make_id("src/Foo.java", "max_ccn"),
        project_ref=PROJECT_REF,
        file_ref=FILE_REF,
        metric_name="max_ccn",
        value=12.0,
    )
    assert m.kind == EntityKind.FILE_METRIC
    assert m.metric_name == "max_ccn"
    assert m.value == 12.0
    assert m.source == "lizard"
    assert m.functions == []


def test_file_metric_make_id_format():
    assert FileMetric.make_id("src/Foo.java", "max_ccn") == "src/Foo.java#max_ccn"


def test_file_metric_with_function_rollup():
    fm = FunctionMetric(
        name="run",
        long_name="Foo::run(int)",
        nloc=20,
        cyclomatic_complexity=4,
        parameters=1,
        token_count=80,
        length=22,
        start_line=10,
        end_line=32,
        class_name="Foo",
    )
    m = FileMetric(
        id=FileMetric.make_id("src/Foo.java", "function_count"),
        project_ref=PROJECT_REF,
        file_ref=FILE_REF,
        metric_name="function_count",
        value=1.0,
        functions=[fm],
    )
    assert m.functions[0].name == "run"
    assert m.functions[0].cyclomatic_complexity == 4


def test_file_metric_rejects_legacy_uuid_field():
    """Legacy carried ``id: uuid.UUID`` — v2 uses str composite id."""
    with pytest.raises(ValidationError):
        FileMetric(
            id="x",
            project_ref=PROJECT_REF,
            file_ref=FILE_REF,
            metric_name="x",
            value=1.0,
            sum_nloc=10,  # legacy aggregate field — collapsed into value/metric_name
        )


def test_file_metric_rejects_legacy_file_path_field():
    """Legacy carried ``file_path: str``; v2 uses file_ref: EntityRef."""
    with pytest.raises(ValidationError):
        FileMetric(
            id="x",
            project_ref=PROJECT_REF,
            file_path="src/Foo.java",  # legacy
            metric_name="x",
            value=1.0,
        )


def test_file_metric_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        FileMetric(
            id="x",
            project_ref=PROJECT_REF,
            file_ref=FILE_REF,
            metric_name="x",
            value=1.0,
            mystery=1,  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# FunctionMetric value object
# ---------------------------------------------------------------------------


def test_function_metric_is_frozen_value_object():
    a = FunctionMetric(
        name="run",
        long_name="X::run()",
        nloc=10,
        cyclomatic_complexity=2,
        parameters=0,
        token_count=40,
        length=12,
        start_line=1,
        end_line=12,
    )
    b = FunctionMetric(
        name="run",
        long_name="X::run()",
        nloc=10,
        cyclomatic_complexity=2,
        parameters=0,
        token_count=40,
        length=12,
        start_line=1,
        end_line=12,
    )
    assert a == b
    with pytest.raises(ValidationError):
        a.name = "ohno"  # type: ignore[misc]


def test_function_metric_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        FunctionMetric(
            name="run",
            long_name="X::run()",
            nloc=10,
            cyclomatic_complexity=2,
            parameters=0,
            token_count=40,
            length=12,
            start_line=1,
            end_line=12,
            mystery=1,  # type: ignore[call-arg]
        )
