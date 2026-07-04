"""Quality-domain Transformer tests."""
from __future__ import annotations

import pytest

from src.common.domains import Transformer, TransformResult
from src.common.domains.quality import (
    QualityIssue,
    QualityProject,
    QualityTransformer,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "q-1"


def _build_entity_bundle(source_tool: str = "insider") -> dict:
    project = QualityProject(
        id=PROJECT_ID,
        name="ZEPPELIN",
        source=SourceKind.QUALITY,
        source_tool=source_tool,  # type: ignore[arg-type]
    )
    project_ref = project.ref()
    file_ref = EntityRef(kind=EntityKind.FILE, id="src/A.java")
    issue = QualityIssue(
        id="1",
        project_ref=project_ref,
        file_ref=file_ref,
        rule_id="X",
        category="c",
        source_tool=source_tool,  # type: ignore[arg-type]
    )
    return {"project": project, "quality_issues": [issue]}


def test_quality_transformer_is_a_transformer():
    assert issubclass(QualityTransformer, Transformer)
    assert QualityTransformer.source == SourceKind.QUALITY


def test_quality_transformer_happy_path_insider():
    bundle = _build_entity_bundle(source_tool="insider")
    result = QualityTransformer().transform(bundle)
    assert isinstance(result, TransformResult)
    assert result.project.source_tool == "insider"
    assert set(result.entities) == {EntityKind.QUALITY_ISSUE}
    assert len(result.entities[EntityKind.QUALITY_ISSUE]) == 1


def test_quality_transformer_happy_path_sonar():
    """Same bundle shape works for either source_tool — the transformer
    is tool-agnostic on the entity-bundle path."""
    bundle = _build_entity_bundle(source_tool="sonar")
    result = QualityTransformer().transform(bundle)
    assert result.project.source_tool == "sonar"


def test_quality_transformer_handles_missing_optional_buckets():
    project = QualityProject(id=PROJECT_ID, name="z", source=SourceKind.QUALITY)
    result = QualityTransformer().transform({"project": project})
    assert result.entities[EntityKind.QUALITY_ISSUE] == []


def test_quality_transformer_rejects_missing_project_key():
    with pytest.raises(ValueError, match="'project'"):
        QualityTransformer().transform({"quality_issues": []})


def test_quality_transformer_rejects_wrong_project_type():
    with pytest.raises(TypeError, match="QualityProject"):
        QualityTransformer().transform({"project": "not-a-project"})


def test_quality_transformer_rejects_wrong_entity_in_bucket():
    bundle = _build_entity_bundle()
    bundle["quality_issues"] = [bundle["project"]]
    with pytest.raises(TypeError, match="quality_issues"):
        QualityTransformer().transform(bundle)


def test_quality_transformer_rejects_unknown_bundle_keys():
    bundle = _build_entity_bundle()
    bundle["commits"] = []
    with pytest.raises(ValueError, match="unknown bundle keys"):
        QualityTransformer().transform(bundle)


def test_quality_transformer_rejects_raw_dto_for_now():
    with pytest.raises(NotImplementedError, match="entity-bundle"):
        QualityTransformer().transform(object())
