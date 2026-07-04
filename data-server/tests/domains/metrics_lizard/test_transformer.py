"""Lizard-metrics-domain Transformer tests."""
from __future__ import annotations

import pytest

from src.common.domains import Transformer, TransformResult
from src.common.domains.metrics_lizard import (
    FileMetric,
    LizardMetricsProject,
    LizardMetricsTransformer,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "lz-1"


def _build_entity_bundle() -> dict:
    project = LizardMetricsProject(
        id=PROJECT_ID, name="ZEPPELIN", source=SourceKind.LIZARD
    )
    project_ref = project.ref()
    file_ref = EntityRef(kind=EntityKind.FILE, id="src/A.java")
    m = FileMetric(
        id=FileMetric.make_id(file_ref.id, "max_ccn"),
        project_ref=project_ref,
        file_ref=file_ref,
        metric_name="max_ccn",
        value=12.0,
    )
    return {"project": project, "file_metrics": [m]}


def test_lizard_metrics_transformer_is_a_transformer():
    assert issubclass(LizardMetricsTransformer, Transformer)
    assert LizardMetricsTransformer.source == SourceKind.LIZARD


def test_lizard_metrics_transformer_happy_path():
    bundle = _build_entity_bundle()
    result = LizardMetricsTransformer().transform(bundle)
    assert isinstance(result, TransformResult)
    assert result.project is bundle["project"]
    assert set(result.entities) == {EntityKind.FILE_METRIC}
    assert len(result.entities[EntityKind.FILE_METRIC]) == 1


def test_lizard_metrics_transformer_handles_missing_optional_buckets():
    project = LizardMetricsProject(
        id=PROJECT_ID, name="z", source=SourceKind.LIZARD
    )
    result = LizardMetricsTransformer().transform({"project": project})
    assert result.entities[EntityKind.FILE_METRIC] == []


def test_lizard_metrics_transformer_rejects_missing_project_key():
    with pytest.raises(ValueError, match="'project'"):
        LizardMetricsTransformer().transform({"file_metrics": []})


def test_lizard_metrics_transformer_rejects_wrong_project_type():
    with pytest.raises(TypeError, match="LizardMetricsProject"):
        LizardMetricsTransformer().transform({"project": "not-a-project"})


def test_lizard_metrics_transformer_rejects_wrong_entity_in_bucket():
    bundle = _build_entity_bundle()
    bundle["file_metrics"] = [bundle["project"]]
    with pytest.raises(TypeError, match="file_metrics"):
        LizardMetricsTransformer().transform(bundle)


def test_lizard_metrics_transformer_rejects_unknown_bundle_keys():
    bundle = _build_entity_bundle()
    bundle["commits"] = []
    with pytest.raises(ValueError, match="unknown bundle keys"):
        LizardMetricsTransformer().transform(bundle)


def test_lizard_metrics_transformer_rejects_raw_dto_for_now():
    with pytest.raises(NotImplementedError, match="entity-bundle"):
        LizardMetricsTransformer().transform(object())
