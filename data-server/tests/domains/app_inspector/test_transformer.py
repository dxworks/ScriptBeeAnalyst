"""App-Inspector-domain Transformer tests."""
from __future__ import annotations

import pytest

from src.common.domains import Transformer, TransformResult
from src.common.domains.app_inspector import (
    AppInspectorProject,
    AppInspectorTransformer,
    AppTag,
)
from src.common.kernel import EntityKind, EntityRef, Graph
from src.common.people import SourceKind


PROJECT_ID = "ai-1"
FILE_PATH = "src/Foo.java"
TAG = "appinspector.OS.Network.Connection.Socket"


def _build_entity_bundle() -> dict:
    project = AppInspectorProject(
        id=PROJECT_ID,
        name="ZEPPELIN",
        source=SourceKind.APP_INSPECTOR,
    )
    project_ref = project.ref()
    file_ref = EntityRef(kind=EntityKind.FILE, id=f"{PROJECT_ID}::{FILE_PATH}")
    tag = AppTag(
        id=AppTag.make_id(PROJECT_ID, FILE_PATH, TAG),
        project_ref=project_ref,
        file_ref=file_ref,
        file_path=FILE_PATH,
        tag=TAG,
        strength=7,
    )
    return {"project": project, "app_tags": [tag]}


def test_app_inspector_transformer_is_a_transformer():
    assert issubclass(AppInspectorTransformer, Transformer)
    assert AppInspectorTransformer.source == SourceKind.APP_INSPECTOR


def test_app_inspector_transformer_happy_path():
    bundle = _build_entity_bundle()
    result = AppInspectorTransformer().transform(bundle)
    assert isinstance(result, TransformResult)
    assert isinstance(result.project, AppInspectorProject)
    assert result.project.id == PROJECT_ID
    assert result.project.source_tool == "appinspector"
    assert set(result.entities) == {EntityKind.APP_TAG}
    assert len(result.entities[EntityKind.APP_TAG]) == 1
    assert result.entities[EntityKind.APP_TAG][0].tag == TAG


def test_app_inspector_transformer_handles_missing_optional_buckets():
    project = AppInspectorProject(
        id=PROJECT_ID, name="z", source=SourceKind.APP_INSPECTOR
    )
    result = AppInspectorTransformer().transform({"project": project})
    assert result.entities[EntityKind.APP_TAG] == []


def test_app_inspector_transformer_rejects_missing_project_key():
    with pytest.raises(ValueError, match="'project'"):
        AppInspectorTransformer().transform({"app_tags": []})


def test_app_inspector_transformer_rejects_wrong_project_type():
    with pytest.raises(TypeError, match="AppInspectorProject"):
        AppInspectorTransformer().transform({"project": "not-a-project"})


def test_app_inspector_transformer_rejects_wrong_entity_in_bucket():
    bundle = _build_entity_bundle()
    bundle["app_tags"] = [bundle["project"]]
    with pytest.raises(TypeError, match="app_tags"):
        AppInspectorTransformer().transform(bundle)


def test_app_inspector_transformer_rejects_unknown_bundle_keys():
    bundle = _build_entity_bundle()
    bundle["commits"] = []
    with pytest.raises(ValueError, match="unknown bundle keys"):
        AppInspectorTransformer().transform(bundle)


def test_app_inspector_transformer_rejects_raw_dto_for_now():
    with pytest.raises(NotImplementedError, match="entity-bundle"):
        AppInspectorTransformer().transform(object())


def test_app_inspector_transformer_result_merges_into_graph():
    """The transformer's result populates the matching typed
    registries on a fresh :class:`Graph`."""
    bundle = _build_entity_bundle()
    result = AppInspectorTransformer().transform(bundle)

    graph = Graph(project_id=PROJECT_ID)
    graph.add_project(result.project)
    for kind, entities in result.entities.items():
        reg = graph.registry_for(kind)
        assert reg is not None
        for entity in entities:
            reg.add(entity)

    assert graph.app_inspector_projects.get(PROJECT_ID) is result.project
    expected_id = AppTag.make_id(PROJECT_ID, FILE_PATH, TAG)
    assert graph.app_tags.get(expected_id) is not None
    assert len(graph.app_tags.all()) == 1
