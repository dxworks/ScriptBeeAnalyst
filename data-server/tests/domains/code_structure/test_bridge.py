"""Tests for the JaFax -> v2 code-structure bridge.

Focused on :func:`build_code_structure_bundle` populating ``file_ref``
on :class:`CodeType` / :class:`CodeMethod` / :class:`CodeField` from
JaFax ``Class.fileName``. Methods/fields inherit the ``file_ref`` of
their owning class.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

from src.common.domains.code_structure.bridge import (
    _normalize_file_path,
    build_code_structure_bundle,
)
from src.common.domains.git.models import File
from src.common.kernel import EntityKind, EntityRef


REPO_NAME = "zeppelin"


def _write_jafax(tmp_path: Path, entries: List[dict]) -> Path:
    out = tmp_path / "layout.json"
    out.write_text(json.dumps(entries), encoding="utf-8")
    return out


def _expected_file_ref(rel_path: str) -> EntityRef:
    return EntityRef(
        kind=EntityKind.FILE, id=File.make_id(REPO_NAME, rel_path)
    )


def test_normalize_file_path_doubled_segment_strips_prefix():
    assert (
        _normalize_file_path(
            "/home/x/voyager-target/zeppelin/zeppelin/src/Foo.java",
            "zeppelin",
        )
        == "src/Foo.java"
    )


def test_normalize_file_path_single_segment_strips_prefix():
    assert (
        _normalize_file_path("/zeppelin/src/Foo.java", "zeppelin")
        == "src/Foo.java"
    )


def test_normalize_file_path_passthrough_when_no_anchor():
    assert (
        _normalize_file_path("/no/match/Foo.java", "zeppelin")
        == "/no/match/Foo.java"
    )


def test_code_type_file_ref_populated_from_filename(tmp_path: Path):
    entries: List[dict] = [
        {
            "type": "Class",
            "id": 19,
            "name": "Foo",
            "pack": "com.example",
            "fileName": "/zeppelin/zeppelin/src/Foo.java",
            "containedMethods": [],
            "containedFields": [],
        },
    ]
    bundle = build_code_structure_bundle(
        _write_jafax(tmp_path, entries), REPO_NAME
    )
    [code_type] = bundle["code_types"]
    assert code_type.file_ref == _expected_file_ref("src/Foo.java")


def test_code_type_file_ref_none_when_filename_missing(tmp_path: Path):
    entries: List[dict] = [
        {
            "type": "Class",
            "id": 20,
            "name": "Bar",
            "pack": "com.example",
            "containedMethods": [],
            "containedFields": [],
        },
    ]
    bundle = build_code_structure_bundle(
        _write_jafax(tmp_path, entries), REPO_NAME
    )
    [code_type] = bundle["code_types"]
    assert code_type.file_ref is None


def test_code_type_file_ref_none_when_filename_empty(tmp_path: Path):
    entries: List[dict] = [
        {
            "type": "Class",
            "id": 21,
            "name": "Baz",
            "fileName": "",
            "containedMethods": [],
            "containedFields": [],
        },
    ]
    bundle = build_code_structure_bundle(
        _write_jafax(tmp_path, entries), REPO_NAME
    )
    [code_type] = bundle["code_types"]
    assert code_type.file_ref is None


def test_method_inherits_file_ref_from_owning_class(tmp_path: Path):
    entries: List[Any] = [
        {
            "type": "Class",
            "id": 19,
            "name": "Foo",
            "pack": "com.example",
            "fileName": "/zeppelin/zeppelin/src/Foo.java",
            "containedMethods": [25],
            "containedFields": [],
        },
        {
            "type": "Method",
            "id": 25,
            "name": "run",
            "container": 19,
            "signature": "run()",
        },
    ]
    bundle = build_code_structure_bundle(
        _write_jafax(tmp_path, entries), REPO_NAME
    )
    [code_type] = bundle["code_types"]
    [code_method] = bundle["code_methods"]
    assert code_method.file_ref is not None
    assert code_method.file_ref == code_type.file_ref


def test_field_file_ref_none_when_class_has_no_filename(tmp_path: Path):
    entries: List[Any] = [
        {
            "type": "Class",
            "id": 19,
            "name": "Foo",
            "pack": "com.example",
            "containedMethods": [],
            "containedFields": [50],
        },
        {
            "type": "Attribute",
            "id": 50,
            "name": "counter",
            "container": 19,
            "kind": "Field",
        },
    ]
    bundle = build_code_structure_bundle(
        _write_jafax(tmp_path, entries), REPO_NAME
    )
    [code_field] = bundle["code_fields"]
    assert code_field.file_ref is None


def test_method_file_ref_none_when_container_unknown(tmp_path: Path):
    # Method.container points at a class id that isn't in classes_by_id;
    # defensive lookup must keep file_ref None instead of raising.
    entries: List[Any] = [
        {
            "type": "Method",
            "id": 25,
            "name": "run",
            "container": 999,
            "signature": "run()",
        },
    ]
    bundle = build_code_structure_bundle(
        _write_jafax(tmp_path, entries), REPO_NAME
    )
    [code_method] = bundle["code_methods"]
    assert code_method.file_ref is None


def test_per_row_repo_overrides_anchor(tmp_path: Path):
    # Class["repo"] wins over the function-level anchor.
    entries: List[Any] = [
        {
            "type": "Class",
            "id": 19,
            "name": "Foo",
            "pack": "com.example",
            "fileName": "/repos/other/other/src/Foo.java",
            "repo": "other",
            "containedMethods": [25],
            "containedFields": [],
        },
        {
            "type": "Method",
            "id": 25,
            "name": "run",
            "container": 19,
            "signature": "run()",
        },
    ]
    bundle = build_code_structure_bundle(
        _write_jafax(tmp_path, entries), REPO_NAME
    )
    [code_type] = bundle["code_types"]
    [code_method] = bundle["code_methods"]
    # File ref must point at "other", NOT the anchor "zeppelin".
    assert code_type.file_ref is not None
    assert code_type.file_ref.id == File.make_id("other", "src/Foo.java")
    assert code_method.file_ref == code_type.file_ref
    assert bundle["_meta"]["all_rows_self_repo"] is True


def test_mixed_rows_some_self_repo(tmp_path: Path):
    entries: List[Any] = [
        {
            "type": "Class",
            "id": 19,
            "name": "Foo",
            "pack": "com.example",
            "fileName": "/repos/other/other/src/Foo.java",
            "repo": "other",
            "containedMethods": [],
            "containedFields": [],
        },
        {
            "type": "Class",
            "id": 20,
            "name": "Bar",
            "pack": "com.example",
            "fileName": "/repos/zeppelin/zeppelin/src/Bar.java",
            "containedMethods": [],
            "containedFields": [],
        },
    ]
    bundle = build_code_structure_bundle(
        _write_jafax(tmp_path, entries), REPO_NAME
    )
    types_by_name = {t.simple_name: t for t in bundle["code_types"]}
    assert types_by_name["Foo"].file_ref.id == File.make_id("other", "src/Foo.java")
    # Bar had no `repo` field -> fell back to anchor.
    assert types_by_name["Bar"].file_ref.id == File.make_id(REPO_NAME, "src/Bar.java")
    assert bundle["_meta"]["all_rows_self_repo"] is False


def test_all_rows_fallback_when_no_repo_field(tmp_path: Path):
    entries: List[Any] = [
        {
            "type": "Class",
            "id": 19,
            "name": "Foo",
            "pack": "com.example",
            "fileName": "/repos/zeppelin/zeppelin/src/Foo.java",
            "containedMethods": [],
            "containedFields": [],
        },
    ]
    bundle = build_code_structure_bundle(
        _write_jafax(tmp_path, entries), REPO_NAME
    )
    [code_type] = bundle["code_types"]
    assert code_type.file_ref.id == File.make_id(REPO_NAME, "src/Foo.java")
    assert bundle["_meta"]["all_rows_self_repo"] is False


def test_missing_filename_logs_single_warning(tmp_path, caplog):
    entries: List[Any] = [
        {
            "type": "Class",
            "id": 19,
            "name": "Foo",
            "containedMethods": [],
            "containedFields": [],
        },
        {
            "type": "Class",
            "id": 20,
            "name": "Bar",
            "containedMethods": [],
            "containedFields": [],
        },
    ]
    with caplog.at_level("WARNING"):
        build_code_structure_bundle(
            _write_jafax(tmp_path, entries), REPO_NAME
        )
    missing_warnings = [
        rec
        for rec in caplog.records
        if "missing 'fileName'" in rec.getMessage()
    ]
    assert len(missing_warnings) == 1
