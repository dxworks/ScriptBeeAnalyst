"""Code-structure-domain entity construction tests.

Covers:

* every entity class instantiates with valid fields,
* cross-entity references are :class:`EntityRef` (never Python refs),
* ``extra="forbid"`` rejects unknown attributes (inherited from Entity),
* class attributes propagate (``kind``),
* :pyattr:`CodeStructureProject.kind_of_source` Literal accepts only
  ``"codeframe"`` and rejects anything else.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.common.domains.code_structure import (
    CodeField,
    CodeMethod,
    CodeReference,
    CodeStructureProject,
    CodeType,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "cs-proj-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)
FILE_REF = EntityRef(kind=EntityKind.FILE, id="src/Foo.java")


# ---------------------------------------------------------------------------
# CodeStructureProject
# ---------------------------------------------------------------------------


def test_code_structure_project_construct_and_metadata():
    proj = CodeStructureProject(
        id=PROJECT_ID, name="ZEPPELIN", source=SourceKind.CODE_STRUCTURE
    )
    assert proj.id == PROJECT_ID
    assert proj.kind == EntityKind.PROJECT
    assert proj.source == SourceKind.CODE_STRUCTURE
    assert proj.kind_of_source == "codeframe"  # default
    assert proj.ref() == EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)


def test_code_structure_project_rejects_unknown_kind_of_source():
    with pytest.raises(ValidationError):
        CodeStructureProject(
            id=PROJECT_ID,
            name="z",
            source=SourceKind.CODE_STRUCTURE,
            kind_of_source="famix",  # not in Literal
        )


def test_code_structure_project_transformer_class():
    from src.common.domains.code_structure.transformer import (
        CodeStructureTransformer,
    )

    proj = CodeStructureProject(
        id=PROJECT_ID, name="z", source=SourceKind.CODE_STRUCTURE
    )
    assert proj.transformer_class() is CodeStructureTransformer


def test_code_structure_project_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        CodeStructureProject(
            id=PROJECT_ID,
            name="z",
            source=SourceKind.CODE_STRUCTURE,
            mystery=1,  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# CodeType
# ---------------------------------------------------------------------------


def test_code_type_construct_with_refs():
    parent = EntityRef(kind=EntityKind.CODE_TYPE, id="codeframe:5")
    method_ref = EntityRef(kind=EntityKind.CODE_METHOD, id="codeframe:10")
    field_ref = EntityRef(kind=EntityKind.CODE_FIELD, id="codeframe:11")
    t = CodeType(
        id="codeframe:19",
        project_ref=PROJECT_REF,
        file_ref=FILE_REF,
        fully_qualified_name="com.example.Foo",
        simple_name="Foo",
        type_category="class",
        parent_refs=[parent],
        method_refs=[method_ref],
        field_refs=[field_ref],
        modifiers=["public"],
    )
    assert t.kind == EntityKind.CODE_TYPE
    assert t.ref() == EntityRef(kind=EntityKind.CODE_TYPE, id="codeframe:19")
    assert t.fully_qualified_name == "com.example.Foo"
    assert t.simple_name == "Foo"
    assert t.parent_refs == [parent]
    assert t.method_refs == [method_ref]
    assert t.field_refs == [field_ref]
    assert t.is_external is False


def test_code_type_file_ref_is_optional():
    t = CodeType(
        id="codeframe:99",
        project_ref=PROJECT_REF,
        fully_qualified_name="java.lang.String",
        simple_name="String",
        type_category="class",
        is_external=True,
    )
    assert t.file_ref is None
    assert t.is_external is True


def test_code_type_rejects_legacy_super_class_id():
    """Legacy carried ``super_class_id: str``; v2 collapses into
    ``parent_refs``."""
    with pytest.raises(ValidationError):
        CodeType(
            id="t",
            project_ref=PROJECT_REF,
            fully_qualified_name="x",
            simple_name="x",
            type_category="class",
            super_class_id="other",  # legacy field — dropped
        )


def test_code_type_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        CodeType(
            id="t",
            project_ref=PROJECT_REF,
            fully_qualified_name="x",
            simple_name="x",
            type_category="class",
            qualified_name="x",  # legacy alias — renamed in v2
        )


# ---------------------------------------------------------------------------
# CodeMethod
# ---------------------------------------------------------------------------


def test_code_method_construct_full():
    type_ref = EntityRef(kind=EntityKind.CODE_TYPE, id="codeframe:19")
    callee = EntityRef(kind=EntityKind.CODE_METHOD, id="codeframe:30")
    m = CodeMethod(
        id="codeframe:25",
        project_ref=PROJECT_REF,
        type_ref=type_ref,
        file_ref=FILE_REF,
        name="run",
        signature="run()",
        return_type="void",
        parameters=["int x", "String y"],
        modifiers=["public", "static"],
        line_start=10,
        line_end=30,
        cyclomatic_complexity=4,
        is_constructor=False,
        called_method_refs=[callee],
    )
    assert m.kind == EntityKind.CODE_METHOD
    assert m.type_ref == type_ref
    assert m.called_method_refs == [callee]
    assert m.cyclomatic_complexity == 4


def test_code_method_type_ref_is_optional():
    m = CodeMethod(
        id="codeframe:25", project_ref=PROJECT_REF, name="orphan"
    )
    assert m.type_ref is None
    assert m.file_ref is None
    assert m.parameters == []
    assert m.called_method_refs == []


def test_code_method_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        CodeMethod(
            id="m",
            project_ref=PROJECT_REF,
            name="m",
            parent_type_id="codeframe:19",  # legacy alias — renamed to type_ref
        )


# ---------------------------------------------------------------------------
# CodeField
# ---------------------------------------------------------------------------


def test_code_field_construct():
    type_ref = EntityRef(kind=EntityKind.CODE_TYPE, id="codeframe:19")
    f = CodeField(
        id="codeframe:50",
        project_ref=PROJECT_REF,
        type_ref=type_ref,
        file_ref=FILE_REF,
        name="counter",
        declared_type="int",
        modifiers=["private"],
    )
    assert f.kind == EntityKind.CODE_FIELD
    assert f.type_ref == type_ref
    assert f.declared_type == "int"


def test_code_field_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        CodeField(
            id="f",
            project_ref=PROJECT_REF,
            name="f",
            file_path="src/Foo.java",  # legacy field — renamed to file_ref
        )


# ---------------------------------------------------------------------------
# CodeReference
# ---------------------------------------------------------------------------


def test_code_reference_construct_call():
    src = EntityRef(kind=EntityKind.CODE_METHOD, id="codeframe:25")
    tgt = EntityRef(kind=EntityKind.CODE_METHOD, id="codeframe:30")
    r = CodeReference(
        id="codeframe:ref:1",
        project_ref=PROJECT_REF,
        reference_kind="call",
        source_method_ref=src,
        target_method_ref=tgt,
        location_file_ref=FILE_REF,
        location_line=42,
        weight=2,
    )
    assert r.kind == EntityKind.CODE_REF
    assert r.reference_kind == "call"
    assert r.source_method_ref == src
    assert r.target_method_ref == tgt
    assert r.target_type_ref is None
    assert r.target_field_ref is None
    assert r.weight == 2


def test_code_reference_construct_inheritance():
    """Inheritance/import use source_type_ref + target_type_ref."""
    src = EntityRef(kind=EntityKind.CODE_TYPE, id="codeframe:19")
    tgt = EntityRef(kind=EntityKind.CODE_TYPE, id="codeframe:5")
    r = CodeReference(
        id="codeframe:ref:2",
        project_ref=PROJECT_REF,
        reference_kind="inheritance",
        source_type_ref=src,
        target_type_ref=tgt,
    )
    assert r.source_method_ref is None
    assert r.source_type_ref == src
    assert r.target_type_ref == tgt


def test_code_reference_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        CodeReference(
            id="r",
            project_ref=PROJECT_REF,
            reference_kind="call",
            from_entity_id="x",  # legacy field — renamed in v2
        )
