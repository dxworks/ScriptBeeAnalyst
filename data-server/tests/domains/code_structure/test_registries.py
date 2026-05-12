"""Code-structure-domain registry tests.

Covers every registry's CRUD + declared indexes + pickle round-trip via
:class:`PickleStore`.
"""
from __future__ import annotations

from pathlib import Path

from src.common.domains.code_structure import (
    CodeField,
    CodeFieldRegistry,
    CodeMethod,
    CodeMethodRegistry,
    CodeReference,
    CodeReferenceRegistry,
    CodeStructureProject,
    CodeStructureProjectRegistry,
    CodeType,
    CodeTypeRegistry,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind
from src.common.pickle_store import PickleStore


PROJECT_ID = "cs-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)
FILE_A_REF = EntityRef(kind=EntityKind.FILE, id="src/Foo.java")
FILE_B_REF = EntityRef(kind=EntityKind.FILE, id="src/Bar.java")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _type(
    tid: str,
    fqn: str,
    simple: str,
    file_ref: EntityRef | None = FILE_A_REF,
    project_ref: EntityRef = PROJECT_REF,
) -> CodeType:
    return CodeType(
        id=tid,
        project_ref=project_ref,
        file_ref=file_ref,
        fully_qualified_name=fqn,
        simple_name=simple,
        type_category="class",
    )


def _method(mid: str, name: str, type_ref: EntityRef | None) -> CodeMethod:
    return CodeMethod(
        id=mid, project_ref=PROJECT_REF, type_ref=type_ref, name=name
    )


def _field(fid: str, name: str, type_ref: EntityRef | None) -> CodeField:
    return CodeField(
        id=fid, project_ref=PROJECT_REF, type_ref=type_ref, name=name
    )


# ---------------------------------------------------------------------------
# CodeStructureProjectRegistry
# ---------------------------------------------------------------------------


def test_code_structure_project_registry_indexes():
    reg = CodeStructureProjectRegistry()
    p1 = CodeStructureProject(
        id="p1",
        name="A",
        source=SourceKind.CODE_STRUCTURE,
        kind_of_source="jafax",
    )
    p2 = CodeStructureProject(
        id="p2",
        name="A",
        source=SourceKind.CODE_STRUCTURE,
        kind_of_source="codeframe",
    )
    p3 = CodeStructureProject(
        id="p3",
        name="B",
        source=SourceKind.CODE_STRUCTURE,
        kind_of_source="jafax",
    )
    reg.add(p1)
    reg.add(p2)
    reg.add(p3)
    assert {p.id for p in reg.by_name["A"]} == {"p1", "p2"}
    assert {p.id for p in reg.by_kind_of_source["jafax"]} == {"p1", "p3"}
    assert {p.id for p in reg.by_kind_of_source["codeframe"]} == {"p2"}


# ---------------------------------------------------------------------------
# CodeTypeRegistry
# ---------------------------------------------------------------------------


def test_code_type_registry_indexes():
    reg = CodeTypeRegistry()
    t1 = _type("t1", "com.example.Foo", "Foo")
    t2 = _type("t2", "com.example.Bar", "Bar", file_ref=FILE_B_REF)
    t3 = _type("t3", "com.other.Foo", "Foo", file_ref=FILE_B_REF)
    t_ext = _type("t4", "java.lang.String", "String", file_ref=None)
    reg.add(t1)
    reg.add(t2)
    reg.add(t3)
    reg.add(t_ext)

    assert {t.id for t in reg.by_file[FILE_A_REF]} == {"t1"}
    assert {t.id for t in reg.by_file[FILE_B_REF]} == {"t2", "t3"}
    # None file_ref is skipped from the by_file index.
    assert reg.by_file[None] == ()
    assert {t.id for t in reg.by_project[PROJECT_REF]} == {"t1", "t2", "t3", "t4"}
    assert {t.id for t in reg.by_simple_name["Foo"]} == {"t1", "t3"}
    assert {t.id for t in reg.by_fqn["com.example.Foo"]} == {"t1"}


# ---------------------------------------------------------------------------
# CodeMethodRegistry
# ---------------------------------------------------------------------------


def test_code_method_registry_indexes():
    reg = CodeMethodRegistry()
    t1 = EntityRef(kind=EntityKind.CODE_TYPE, id="t1")
    t2 = EntityRef(kind=EntityKind.CODE_TYPE, id="t2")
    reg.add(_method("m1", "run", t1))
    reg.add(_method("m2", "run", t2))
    reg.add(_method("m3", "stop", t1))
    reg.add(_method("orphan", "free", None))

    assert {m.id for m in reg.by_type[t1]} == {"m1", "m3"}
    assert {m.id for m in reg.by_type[t2]} == {"m2"}
    assert reg.by_type[None] == ()
    assert {m.id for m in reg.by_name["run"]} == {"m1", "m2"}
    assert {m.id for m in reg.by_project[PROJECT_REF]} == {"m1", "m2", "m3", "orphan"}


# ---------------------------------------------------------------------------
# CodeFieldRegistry
# ---------------------------------------------------------------------------


def test_code_field_registry_indexes():
    reg = CodeFieldRegistry()
    t1 = EntityRef(kind=EntityKind.CODE_TYPE, id="t1")
    reg.add(_field("f1", "counter", t1))
    reg.add(_field("f2", "counter", None))
    reg.add(_field("f3", "size", t1))

    assert {f.id for f in reg.by_name["counter"]} == {"f1", "f2"}
    assert {f.id for f in reg.by_type[t1]} == {"f1", "f3"}
    assert reg.by_type[None] == ()
    assert {f.id for f in reg.by_project[PROJECT_REF]} == {"f1", "f2", "f3"}


# ---------------------------------------------------------------------------
# CodeReferenceRegistry
# ---------------------------------------------------------------------------


def test_code_reference_registry_indexes():
    reg = CodeReferenceRegistry()
    m_a = EntityRef(kind=EntityKind.CODE_METHOD, id="m_a")
    m_b = EntityRef(kind=EntityKind.CODE_METHOD, id="m_b")
    t_x = EntityRef(kind=EntityKind.CODE_TYPE, id="t_x")
    t_y = EntityRef(kind=EntityKind.CODE_TYPE, id="t_y")
    f_p = EntityRef(kind=EntityKind.CODE_FIELD, id="f_p")
    call = CodeReference(
        id="r1",
        project_ref=PROJECT_REF,
        reference_kind="call",
        source_method_ref=m_a,
        target_method_ref=m_b,
    )
    inherits = CodeReference(
        id="r2",
        project_ref=PROJECT_REF,
        reference_kind="inheritance",
        source_type_ref=t_x,
        target_type_ref=t_y,
    )
    field_read = CodeReference(
        id="r3",
        project_ref=PROJECT_REF,
        reference_kind="field_read",
        source_method_ref=m_a,
        target_field_ref=f_p,
    )
    reg.add(call)
    reg.add(inherits)
    reg.add(field_read)

    # by_source: picks the non-None source ref of each row
    assert {r.id for r in reg.by_source[m_a]} == {"r1", "r3"}
    assert {r.id for r in reg.by_source[t_x]} == {"r2"}
    # by_target: picks the non-None target ref of each row
    assert {r.id for r in reg.by_target[m_b]} == {"r1"}
    assert {r.id for r in reg.by_target[t_y]} == {"r2"}
    assert {r.id for r in reg.by_target[f_p]} == {"r3"}
    # by_kind
    assert {r.id for r in reg.by_kind["call"]} == {"r1"}
    assert {r.id for r in reg.by_kind["inheritance"]} == {"r2"}
    assert {r.id for r in reg.by_kind["field_read"]} == {"r3"}
    assert {r.id for r in reg.by_project[PROJECT_REF]} == {"r1", "r2", "r3"}


def test_code_type_registry_remove_updates_indexes():
    reg = CodeTypeRegistry()
    t = _type("t1", "com.example.Foo", "Foo")
    reg.add(t)
    assert reg.by_simple_name["Foo"] == (t,)
    reg.remove(t.id)
    assert reg.by_simple_name["Foo"] == ()
    assert reg.by_file[FILE_A_REF] == ()


# ---------------------------------------------------------------------------
# Pickle round-trip
# ---------------------------------------------------------------------------


def test_code_type_registry_pickle_round_trip(tmp_path: Path):
    reg = CodeTypeRegistry()
    reg.add(_type("t1", "com.example.Foo", "Foo"))
    reg.add(_type("t2", "com.example.Bar", "Bar", file_ref=FILE_B_REF))
    store = PickleStore(tmp_path)
    store.write_registry(EntityKind.CODE_TYPE.value, reg)
    restored = store.read_registry(EntityKind.CODE_TYPE.value, CodeTypeRegistry)
    assert len(restored) == 2
    assert {t.id for t in restored.by_simple_name["Foo"]} == {"t1"}
    assert {t.id for t in restored.by_file[FILE_B_REF]} == {"t2"}
