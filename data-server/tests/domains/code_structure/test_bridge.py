"""Tests for the CodeFrame -> v2 code-structure bridge.

Covers :func:`build_code_structure_bundle` ingesting a CodeFrame JSONL
dump. Fixtures are small inline JSONL documents written to ``tmp_path``;
the on-disk CodeFrame sample is intentionally NOT used here so tests
stay hermetic.

Scenarios:

* Path-normalization helper (doubled / single / passthrough segments).
* A Java file with ``implementsInterfaces``, methods (one with a
  resolvable + one with an unresolvable ``methodCall``), and ``fields[]``.
* A JS file with file-scope ``methods[]`` only — no owning type.
* A Java file with a nested inner class.
* ``_meta`` exposure and ``kind_of_source == "codeframe"`` on the project.
* The bridge's INFO "resolved X / Y method calls" line.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List

from src.common.domains.code_structure.bridge import (
    _normalize_file_path,
    build_code_structure_bundle,
)
from src.common.domains.git.models import File
from src.common.kernel import EntityKind, EntityRef


REPO_NAME = "zeppelin"


def _write_jsonl(tmp_path: Path, lines: List[dict], name: str = "zeppelin-codeframe.jsonl") -> Path:
    """Materialize an inline CodeFrame JSONL fixture."""
    out = tmp_path / name
    with open(out, "w", encoding="utf-8") as stream:
        for line in lines:
            stream.write(json.dumps(line))
            stream.write("\n")
    return out


def _expected_file_ref(rel_path: str, repo: str = REPO_NAME) -> EntityRef:
    return EntityRef(kind=EntityKind.FILE, id=File.make_id(repo, rel_path))


# ---------------------------------------------------------------------------
# Path-normalization helper
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Fixture: a header + Java file (interface + 2 methods + fields) + JS
#          file (file-scope methods only) + inner class + footer.
# ---------------------------------------------------------------------------


JAVA_PATH = "/voyager-target/zeppelin/zeppelin/src/main/java/com/example/CacheFilter.java"
JS_PATH = "/voyager-target/zeppelin/zeppelin/web/static/widget.js"
INNER_PATH = "/voyager-target/zeppelin/zeppelin/src/main/java/com/example/Outer.java"


def _full_fixture() -> List[dict]:
    return [
        {
            "kind": "run",
            "total_files": 3,
            "started_at": "2026-05-22T00:00:00Z",
            "input_path": "/voyager-target/zeppelin",
        },
        # Java file: declares com.example.CacheFilter implementing
        # ContainerResponseFilter; has 2 methods, one of which calls a
        # method on the (also-defined) ContainerResponseFilter and one
        # which calls something external (unresolvable).
        {
            "filePath": JAVA_PATH,
            "language": "java",
            "packageName": "com.example",
            "imports": ["import com.example.ContainerResponseFilter;"],
            "types": [
                {
                    "kind": "class",
                    "name": "CacheFilter",
                    "visibility": "public",
                    "modifiers": ["public"],
                    "implementsInterfaces": ["ContainerResponseFilter"],
                    "methods": [
                        {
                            "name": "filter",
                            "returnType": "void",
                            "visibility": "public",
                            "modifiers": ["public"],
                            "parameters": [
                                {"name": "req", "type": "Request"},
                                {"name": "res", "type": "Response"},
                            ],
                            "methodCalls": [
                                # Resolvable: objectType.method matches
                                # ContainerResponseFilter.handle below.
                                {
                                    "methodName": "handle",
                                    "objectType": "ContainerResponseFilter",
                                    "objectName": "iface",
                                    "callCount": 2,
                                    "parameterCount": 1,
                                },
                                # Unresolvable: the simple-name "equals"
                                # isn't declared anywhere in this fixture.
                                {
                                    "methodName": "equals",
                                    "callCount": 1,
                                    "parameterCount": 1,
                                },
                            ],
                        },
                        {
                            "name": "CacheFilter",
                            "visibility": "public",
                            "modifiers": ["public"],
                            "parameters": [],
                        },
                    ],
                    "fields": [
                        {
                            "name": "LOGGER",
                            "type": "Logger",
                            "visibility": "private",
                            "modifiers": ["private", "final", "static"],
                        },
                        {
                            "name": "cacheControl",
                            "type": "CacheControl",
                            "visibility": "public",
                            "modifiers": ["public"],
                        },
                    ],
                },
                {
                    "kind": "interface",
                    "name": "ContainerResponseFilter",
                    "visibility": "public",
                    "modifiers": ["public"],
                    "methods": [
                        {
                            "name": "handle",
                            "returnType": "void",
                            "visibility": "public",
                            "modifiers": ["public", "abstract"],
                            "parameters": [
                                {"name": "ctx", "type": "Context"},
                            ],
                        },
                    ],
                },
            ],
        },
        # JS file: only file-scope free functions, no types.
        {
            "filePath": JS_PATH,
            "language": "javascript",
            "methods": [
                {
                    "name": "renderWidget",
                    "parameters": [
                        {"name": "el", "type": "HTMLElement"},
                    ],
                    "methodCalls": [
                        # Resolvable simple-name (only one matching
                        # 'computeDom' in the whole fixture).
                        {"methodName": "computeDom", "callCount": 1},
                    ],
                },
                {
                    "name": "computeDom",
                    "parameters": [],
                },
            ],
        },
        # Java file with an inner class.
        {
            "filePath": INNER_PATH,
            "language": "java",
            "packageName": "com.example",
            "types": [
                {
                    "kind": "class",
                    "name": "Outer",
                    "visibility": "public",
                    "modifiers": ["public"],
                    "methods": [],
                    "types": [
                        {
                            "kind": "class",
                            "name": "Inner",
                            "visibility": "private",
                            "modifiers": ["private", "static"],
                            "methods": [
                                {
                                    "name": "run",
                                    "returnType": "void",
                                    "visibility": "public",
                                    "modifiers": ["public"],
                                    "parameters": [],
                                },
                            ],
                        },
                    ],
                },
            ],
        },
        {
            "kind": "done",
            "files_analyzed": 3,
            "files_with_errors": 0,
            "duration_seconds": 0.01,
            "ended_at": "2026-05-22T00:00:01Z",
        },
    ]


# ---------------------------------------------------------------------------
# Bundle shape
# ---------------------------------------------------------------------------


def test_bundle_shape_and_project_metadata(tmp_path: Path):
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME, "Zeppelin"
    )
    assert set(bundle.keys()) == {
        "project",
        "code_types",
        "code_methods",
        "code_fields",
        "code_refs",
        "_meta",
    }
    project = bundle["project"]
    assert project.id == REPO_NAME
    assert project.name == "Zeppelin"
    assert project.kind_of_source == "codeframe"
    assert bundle["_meta"] == {"all_rows_self_repo": False}


def test_default_project_name_defaults_to_project(tmp_path: Path):
    # Match the public signature default: project_name defaults to
    # "Project".
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
    )
    assert bundle["project"].name == "Project"


# ---------------------------------------------------------------------------
# CodeType emission (including inner classes)
# ---------------------------------------------------------------------------


def test_code_types_emit_one_per_type_including_inner(tmp_path: Path):
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
    )
    types_by_name = {t.simple_name: t for t in bundle["code_types"]}
    assert set(types_by_name) == {
        "CacheFilter",
        "ContainerResponseFilter",
        "Outer",
        "Inner",
    }
    # Inner class is reachable at the same flat list level.
    inner = types_by_name["Inner"]
    assert inner.type_category == "class"
    assert "private" in inner.modifiers
    assert "static" in inner.modifiers


def test_code_type_carries_fqn_and_file_ref(tmp_path: Path):
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
    )
    types_by_name = {t.simple_name: t for t in bundle["code_types"]}
    cf = types_by_name["CacheFilter"]
    assert cf.fully_qualified_name == "com.example.CacheFilter"
    assert cf.type_category == "class"
    # JAVA_PATH starts with "/voyager-target/zeppelin/zeppelin/...";
    # the doubled-segment strip should reduce it to the relative path.
    assert cf.file_ref == _expected_file_ref(
        "src/main/java/com/example/CacheFilter.java"
    )
    # Visibility folded into modifiers.
    assert "public" in cf.modifiers


def test_interface_kind_preserved(tmp_path: Path):
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
    )
    types_by_name = {t.simple_name: t for t in bundle["code_types"]}
    iface = types_by_name["ContainerResponseFilter"]
    assert iface.type_category == "interface"


def test_code_type_method_and_field_refs_populated(tmp_path: Path):
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
    )
    types_by_name = {t.simple_name: t for t in bundle["code_types"]}
    cf = types_by_name["CacheFilter"]
    # CacheFilter has 2 methods + 2 fields in the fixture.
    assert len(cf.method_refs) == 2
    assert len(cf.field_refs) == 2
    for ref in cf.method_refs:
        assert ref.kind == EntityKind.CODE_METHOD
    for ref in cf.field_refs:
        assert ref.kind == EntityKind.CODE_FIELD


# ---------------------------------------------------------------------------
# Interface (implementsInterfaces) refs
# ---------------------------------------------------------------------------


def test_implements_interface_emits_interface_ref(tmp_path: Path):
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
    )
    refs_by_kind = _by_kind(bundle["code_refs"])
    iface_refs = refs_by_kind.get("interface", [])
    assert len(iface_refs) == 1

    types_by_name = {t.simple_name: t for t in bundle["code_types"]}
    cf_ref = types_by_name["CacheFilter"].ref()
    iface_target = types_by_name["ContainerResponseFilter"].ref()
    ref = iface_refs[0]
    assert ref.source_type_ref == cf_ref
    assert ref.target_type_ref == iface_target


def test_unknown_interface_name_dropped(tmp_path: Path):
    fixture = _full_fixture()
    # CacheFilter is index 1.
    cf = fixture[1]["types"][0]
    cf["implementsInterfaces"] = ["NotDeclaredAnywhere"]
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, fixture), REPO_NAME
    )
    refs_by_kind = _by_kind(bundle["code_refs"])
    assert refs_by_kind.get("interface", []) == []
    types_by_name = {t.simple_name: t for t in bundle["code_types"]}
    # parent_refs should also be empty since the name didn't resolve.
    assert types_by_name["CacheFilter"].parent_refs == []


# ---------------------------------------------------------------------------
# CodeMethod emission, including file-scope free functions
# ---------------------------------------------------------------------------


def test_method_signature_and_parameters_include_param_types(tmp_path: Path):
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
    )
    methods_by_name = _methods_by_name(bundle["code_methods"])
    filter_m = _only_for_type(methods_by_name["filter"], "com.example.CacheFilter")
    assert filter_m.signature == "filter(Request,Response)"
    assert filter_m.parameters == ["req: Request", "res: Response"]
    assert filter_m.return_type == "void"
    assert filter_m.cyclomatic_complexity == 0
    assert filter_m.line_start is None and filter_m.line_end is None


def test_constructor_flagged_when_method_name_matches_type(tmp_path: Path):
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
    )
    # CacheFilter has a same-name "CacheFilter" method modelling a Java ctor.
    methods = [
        m
        for m in bundle["code_methods"]
        if m.name == "CacheFilter"
    ]
    assert len(methods) == 1
    assert methods[0].is_constructor is True


def test_free_function_has_no_type_ref(tmp_path: Path):
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
    )
    methods_by_name = _methods_by_name(bundle["code_methods"])
    render = methods_by_name["renderWidget"]
    assert len(render) == 1
    assert render[0].type_ref is None
    # File-scope free functions still carry a file_ref derived from the
    # JS path.
    assert render[0].file_ref == _expected_file_ref("web/static/widget.js")


# ---------------------------------------------------------------------------
# CodeField emission
# ---------------------------------------------------------------------------


def test_code_fields_emitted_with_declared_type_and_visibility(tmp_path: Path):
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
    )
    fields_by_name = {f.name: f for f in bundle["code_fields"]}
    assert set(fields_by_name) == {"LOGGER", "cacheControl"}
    logger_field = fields_by_name["LOGGER"]
    assert logger_field.declared_type == "Logger"
    assert "private" in logger_field.modifiers
    assert "static" in logger_field.modifiers
    # File ref inherited from the owning class.
    assert logger_field.file_ref == _expected_file_ref(
        "src/main/java/com/example/CacheFilter.java"
    )


# ---------------------------------------------------------------------------
# Call edges
# ---------------------------------------------------------------------------


def test_method_call_resolved_via_object_type(tmp_path: Path):
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
    )
    refs_by_kind = _by_kind(bundle["code_refs"])
    call_refs = refs_by_kind.get("call", [])
    # Two resolvable calls in the fixture: filter -> handle (Java),
    # renderWidget -> computeDom (JS). The Java filter's "equals" call
    # is intentionally unresolvable and must be dropped.
    assert len(call_refs) == 2

    methods_by_name = _methods_by_name(bundle["code_methods"])
    filter_m = _only_for_type(methods_by_name["filter"], "com.example.CacheFilter")
    handle_m = _only_for_type(
        methods_by_name["handle"], "com.example.ContainerResponseFilter"
    )
    render_m = methods_by_name["renderWidget"][0]
    compute_m = methods_by_name["computeDom"][0]

    targets_by_source = {
        ref.source_method_ref.id: ref for ref in call_refs
    }
    java_call = targets_by_source[filter_m.ref().id]
    assert java_call.target_method_ref == handle_m.ref()
    # callCount == 2 in the fixture, surfaced as weight.
    assert java_call.weight == 2

    js_call = targets_by_source[render_m.ref().id]
    assert js_call.target_method_ref == compute_m.ref()


def test_unresolvable_call_silently_dropped(tmp_path: Path):
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
    )
    methods_by_name = _methods_by_name(bundle["code_methods"])
    filter_m = _only_for_type(methods_by_name["filter"], "com.example.CacheFilter")
    # called_method_refs caches only the resolvable callee (handle).
    assert len(filter_m.called_method_refs) == 1


def test_resolved_count_logged_at_info(tmp_path: Path, caplog):
    with caplog.at_level(logging.INFO):
        build_code_structure_bundle(
            _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
        )
    matching = [
        rec for rec in caplog.records
        if "resolved" in rec.getMessage() and "method calls" in rec.getMessage()
    ]
    assert len(matching) == 1
    # Three calls in the fixture total (handle, equals, computeDom);
    # two are resolvable.
    assert "2 / 3" in matching[0].getMessage()


# ---------------------------------------------------------------------------
# IDs use the codeframe: prefix and never the jafax: prefix
# ---------------------------------------------------------------------------


def test_no_jafax_strings_in_emitted_ids(tmp_path: Path):
    bundle = build_code_structure_bundle(
        _write_jsonl(tmp_path, _full_fixture()), REPO_NAME
    )
    for collection in (
        bundle["code_types"],
        bundle["code_methods"],
        bundle["code_fields"],
        bundle["code_refs"],
    ):
        for ent in collection:
            assert "jafax" not in ent.id
            assert ent.id.startswith("codeframe:")


# ---------------------------------------------------------------------------
# Robustness: empty / malformed input
# ---------------------------------------------------------------------------


def test_empty_jsonl_returns_empty_bundle(tmp_path: Path):
    out = tmp_path / "empty.jsonl"
    out.write_text("", encoding="utf-8")
    bundle = build_code_structure_bundle(out, REPO_NAME)
    assert bundle["code_types"] == []
    assert bundle["code_methods"] == []
    assert bundle["code_fields"] == []
    assert bundle["code_refs"] == []
    assert bundle["project"].kind_of_source == "codeframe"


def test_blank_lines_tolerated(tmp_path: Path):
    raw = "\n".join(
        ["", json.dumps({"kind": "run", "total_files": 0}), "", json.dumps({"kind": "done"}), ""]
    )
    out = tmp_path / "blanks.jsonl"
    out.write_text(raw, encoding="utf-8")
    bundle = build_code_structure_bundle(out, REPO_NAME)
    assert bundle["code_types"] == []


def test_malformed_json_raises_value_error(tmp_path: Path):
    out = tmp_path / "bad.jsonl"
    out.write_text("{this is not json}\n", encoding="utf-8")
    try:
        build_code_structure_bundle(out, REPO_NAME)
    except ValueError as exc:
        assert "line 1" in str(exc)
    else:  # pragma: no cover - assertion-only branch
        raise AssertionError("Expected ValueError on malformed JSON")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _by_kind(refs):
    by: dict = {}
    for ref in refs:
        by.setdefault(ref.reference_kind, []).append(ref)
    return by


def _methods_by_name(methods) -> dict:
    by: dict = {}
    for m in methods:
        by.setdefault(m.name, []).append(m)
    return by


def _only_for_type(candidates, type_fqn: str):
    matching = [m for m in candidates if m.type_ref and type_fqn in m.type_ref.id]
    assert len(matching) == 1, (
        f"Expected exactly one method with type_ref containing {type_fqn!r}; "
        f"got {[m.id for m in matching]}"
    )
    return matching[0]
