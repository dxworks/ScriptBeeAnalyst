"""App-Inspector-domain entity construction tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.common.domains.app_inspector import (
    AppInspectorProject,
    AppTag,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "ai-proj-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)
FILE_PATH = "src/Foo.java"
FILE_REF = EntityRef(kind=EntityKind.FILE, id=f"{PROJECT_ID}::{FILE_PATH}")
TAG = "appinspector.OS.Network.Connection.Socket"


def test_app_inspector_project_construct_defaults_to_appinspector():
    p = AppInspectorProject(
        id=PROJECT_ID, name="ZEPPELIN", source=SourceKind.APP_INSPECTOR
    )
    assert p.kind == EntityKind.PROJECT
    assert p.source == SourceKind.APP_INSPECTOR
    assert p.source_tool == "appinspector"  # default


def test_app_inspector_project_rejects_unknown_source_tool():
    with pytest.raises(ValidationError):
        AppInspectorProject(
            id=PROJECT_ID,
            name="z",
            source=SourceKind.APP_INSPECTOR,
            source_tool="chronos",  # not in Literal
        )


def test_app_inspector_project_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        AppInspectorProject(
            id=PROJECT_ID,
            name="z",
            source=SourceKind.APP_INSPECTOR,
            mystery=1,  # type: ignore[call-arg]
        )


def test_app_tag_construct_all_fields():
    tag = AppTag(
        id=AppTag.make_id(PROJECT_ID, FILE_PATH, TAG),
        project_ref=PROJECT_REF,
        file_ref=FILE_REF,
        file_path=FILE_PATH,
        tag=TAG,
        strength=7,
    )
    assert tag.kind == EntityKind.APP_TAG
    assert tag.project_ref == PROJECT_REF
    assert tag.file_ref == FILE_REF
    assert tag.file_path == FILE_PATH
    assert tag.tag == TAG
    assert tag.strength == 7


def test_app_tag_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        AppTag(
            id="x",
            project_ref=PROJECT_REF,
            file_ref=FILE_REF,
            file_path=FILE_PATH,
            tag=TAG,
            strength=1,
            mystery=42,  # type: ignore[call-arg]
        )


def test_app_tag_requires_strength():
    with pytest.raises(ValidationError):
        AppTag(
            id="x",
            project_ref=PROJECT_REF,
            file_ref=FILE_REF,
            file_path=FILE_PATH,
            tag=TAG,
        )


def test_app_tag_id_is_stable_for_same_inputs():
    """Same (project_id, file_path, tag) → same id."""
    a = AppTag.make_id(PROJECT_ID, FILE_PATH, TAG)
    b = AppTag.make_id(PROJECT_ID, FILE_PATH, TAG)
    assert a == b


def test_app_tag_id_differs_on_different_file_path():
    a = AppTag.make_id(PROJECT_ID, "src/Foo.java", TAG)
    b = AppTag.make_id(PROJECT_ID, "src/Bar.java", TAG)
    assert a != b


def test_app_tag_id_differs_on_different_tag():
    a = AppTag.make_id(
        PROJECT_ID, FILE_PATH, "appinspector.OS.Network.Connection.Socket"
    )
    b = AppTag.make_id(
        PROJECT_ID, FILE_PATH, "appinspector.Cryptography.CryptoCurrency"
    )
    assert a != b


def test_app_tag_id_differs_on_different_project():
    a = AppTag.make_id("project-A", FILE_PATH, TAG)
    b = AppTag.make_id("project-B", FILE_PATH, TAG)
    assert a != b
