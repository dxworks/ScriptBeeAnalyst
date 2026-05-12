"""Code-structure-domain Transformer tests.

Parallel to ``tests/domains/jira/test_transformer.py``. The transformer
accepts pre-built entity bundles and delegates validation to the shared
:meth:`Transformer.collect_bundle` helper. The raw-DTO path is deferred
to Chunk 8.
"""
from __future__ import annotations

import pytest

from src.common.domains import Transformer, TransformResult
from src.common.domains.code_structure import (
    CodeField,
    CodeMethod,
    CodeReference,
    CodeStructureProject,
    CodeStructureTransformer,
    CodeType,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "cs-1"


def _build_entity_bundle() -> dict:
    project = CodeStructureProject(
        id=PROJECT_ID,
        name="ZEPPELIN",
        source=SourceKind.CODE_STRUCTURE,
        kind_of_source="jafax",
    )
    project_ref = project.ref()
    file_ref = EntityRef(kind=EntityKind.FILE, id="src/Foo.java")
    t = CodeType(
        id="jafax:19",
        project_ref=project_ref,
        file_ref=file_ref,
        fully_qualified_name="com.example.Foo",
        simple_name="Foo",
        type_category="class",
    )
    m = CodeMethod(
        id="jafax:25",
        project_ref=project_ref,
        type_ref=t.ref(),
        name="run",
    )
    f = CodeField(
        id="jafax:50",
        project_ref=project_ref,
        type_ref=t.ref(),
        name="counter",
    )
    r = CodeReference(
        id="jafax:ref:1",
        project_ref=project_ref,
        reference_kind="call",
        source_method_ref=m.ref(),
        target_method_ref=m.ref(),
    )
    return {
        "project": project,
        "code_types": [t],
        "code_methods": [m],
        "code_fields": [f],
        "code_refs": [r],
    }


def test_code_structure_transformer_is_a_transformer():
    assert issubclass(CodeStructureTransformer, Transformer)
    assert CodeStructureTransformer.source == SourceKind.CODE_STRUCTURE


def test_code_structure_transformer_happy_path():
    bundle = _build_entity_bundle()
    result = CodeStructureTransformer().transform(bundle)
    assert isinstance(result, TransformResult)
    assert result.project is bundle["project"]
    assert set(result.entities) == {
        EntityKind.CODE_TYPE,
        EntityKind.CODE_METHOD,
        EntityKind.CODE_FIELD,
        EntityKind.CODE_REF,
    }
    assert [t.id for t in result.entities[EntityKind.CODE_TYPE]] == ["jafax:19"]


def test_code_structure_transformer_handles_codeframe_project():
    """``kind_of_source`` is on the project; transformer doesn't care for
    the entity-bundle path — it just forwards everything."""
    bundle = _build_entity_bundle()
    bundle["project"] = CodeStructureProject(
        id=PROJECT_ID,
        name="z",
        source=SourceKind.CODE_STRUCTURE,
        kind_of_source="codeframe",
    )
    result = CodeStructureTransformer().transform(bundle)
    assert result.project.kind_of_source == "codeframe"


def test_code_structure_transformer_handles_missing_optional_buckets():
    bundle = _build_entity_bundle()
    del bundle["code_refs"]
    del bundle["code_fields"]
    result = CodeStructureTransformer().transform(bundle)
    assert result.entities[EntityKind.CODE_REF] == []
    assert result.entities[EntityKind.CODE_FIELD] == []
    assert len(result.entities[EntityKind.CODE_TYPE]) == 1


def test_code_structure_transformer_rejects_missing_project_key():
    with pytest.raises(ValueError, match="'project'"):
        CodeStructureTransformer().transform({"code_types": []})


def test_code_structure_transformer_rejects_wrong_project_type():
    with pytest.raises(TypeError, match="CodeStructureProject"):
        CodeStructureTransformer().transform({"project": "not-a-project"})


def test_code_structure_transformer_rejects_wrong_entity_in_bucket():
    bundle = _build_entity_bundle()
    # A CodeField in the code_types bucket — must be CodeType.
    bundle["code_types"] = [bundle["code_fields"][0]]
    with pytest.raises(TypeError, match="code_types"):
        CodeStructureTransformer().transform(bundle)


def test_code_structure_transformer_rejects_unknown_bundle_keys():
    bundle = _build_entity_bundle()
    bundle["commits"] = []  # git bucket leaked
    with pytest.raises(ValueError, match="unknown bundle keys"):
        CodeStructureTransformer().transform(bundle)


def test_code_structure_transformer_rejects_raw_dto_for_now():
    """Raw JaFax/Codeframe ingestion is deferred to Chunk 8."""
    with pytest.raises(NotImplementedError, match="entity-bundle"):
        CodeStructureTransformer().transform(object())
