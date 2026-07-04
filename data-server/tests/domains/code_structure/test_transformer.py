"""Code-structure-domain Transformer tests.

Parallel to ``tests/domains/jira/test_transformer.py``. The transformer
accepts pre-built entity bundles and delegates validation to the shared
:meth:`Transformer.collect_bundle` helper. Raw CodeFrame JSONL parsing
lives in :mod:`src.common.domains.code_structure.bridge`.
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
        kind_of_source="codeframe",
    )
    project_ref = project.ref()
    file_ref = EntityRef(kind=EntityKind.FILE, id="src/Foo.java")
    t = CodeType(
        id="codeframe:19",
        project_ref=project_ref,
        file_ref=file_ref,
        fully_qualified_name="com.example.Foo",
        simple_name="Foo",
        type_category="class",
    )
    m = CodeMethod(
        id="codeframe:25",
        project_ref=project_ref,
        type_ref=t.ref(),
        name="run",
    )
    f = CodeField(
        id="codeframe:50",
        project_ref=project_ref,
        type_ref=t.ref(),
        name="counter",
    )
    r = CodeReference(
        id="codeframe:ref:1",
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
    assert [t.id for t in result.entities[EntityKind.CODE_TYPE]] == ["codeframe:19"]


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


def test_code_structure_transformer_rejects_raw_dto():
    """Raw CodeFrame JSONL must go through the bridge, not the transformer."""
    with pytest.raises(NotImplementedError, match="entity-bundle"):
        CodeStructureTransformer().transform(object())
