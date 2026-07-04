"""Tests for the App-Inspector -> v2 app_inspector bridge.

Mirrors the patterns from ``tests/domains/quality/test_bridge.py``:

* Test 1 — small hand-built payload.
* Test 2 — real-data fixture (the ``zeppelin-chronos-tags.json`` copy
  vendored under ``resources/``).
* Test 3 — repo-prefix stripping.
* Test 4 — malformed-row handling.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from src.common.domains.app_inspector import (
    AppInspectorProject,
    AppTag,
    build_app_inspector_bundle,
)
from src.common.domains.git.models import File
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


ANCHOR_REPO = "zeppelin"
FIXTURE_PATH = (
    Path(__file__).parent / "resources" / "zeppelin-chronos-tags.json"
)


def _write_payload(tmp_path: Path, payload: Mapping[str, Any]) -> Path:
    out = tmp_path / "chronos-tags.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


def _wrap(concerns: list[dict]) -> dict:
    return {"file": {"concerns": concerns}}


# ---------------------------------------------------------------------------
# Test 1 — small hand-built payload
# ---------------------------------------------------------------------------


def test_small_hand_built_payload_builds_typed_bundle(tmp_path: Path):
    payload = _wrap(
        [
            {
                "entity": f"{ANCHOR_REPO}/src/Foo.java",
                "tag": "appinspector.OS.Network.Connection.Socket",
                "strength": 18,
            },
            {
                "entity": f"{ANCHOR_REPO}/src/Foo.java",
                "tag": "appinspector.Cryptography.CryptoCurrency",
                "strength": 3,
            },
            {
                "entity": f"{ANCHOR_REPO}/src/Bar.java",
                "tag": "appinspector.OS.Network.Connection.Socket",
                "strength": 1,
            },
        ]
    )
    bundle = build_app_inspector_bundle(
        _write_payload(tmp_path, payload), ANCHOR_REPO, project_name="Zeppelin"
    )

    project = bundle["project"]
    assert isinstance(project, AppInspectorProject)
    assert project.id == ANCHOR_REPO
    assert project.name == "Zeppelin"
    assert project.source == SourceKind.APP_INSPECTOR
    assert project.source_tool == "appinspector"

    tags = bundle["app_tags"]
    assert len(tags) == 3
    assert all(isinstance(t, AppTag) for t in tags)

    # Ids are stable for (project_id, file_path, tag).
    ids = {t.id for t in tags}
    assert AppTag.make_id(
        ANCHOR_REPO, "src/Foo.java", "appinspector.OS.Network.Connection.Socket"
    ) in ids
    assert AppTag.make_id(
        ANCHOR_REPO, "src/Foo.java", "appinspector.Cryptography.CryptoCurrency"
    ) in ids
    assert AppTag.make_id(
        ANCHOR_REPO, "src/Bar.java", "appinspector.OS.Network.Connection.Socket"
    ) in ids

    # file_ref / file_path follow the strip-the-repo convention.
    first = next(
        t for t in tags
        if t.file_path == "src/Foo.java"
        and t.tag == "appinspector.OS.Network.Connection.Socket"
    )
    assert first.file_ref == EntityRef(
        kind=EntityKind.FILE,
        id=File.make_id(ANCHOR_REPO, "src/Foo.java"),
    )
    assert first.strength == 18

    assert bundle["_meta"]["all_rows_self_repo"] is True


# ---------------------------------------------------------------------------
# Test 2 — real chronos fixture
# ---------------------------------------------------------------------------


def test_real_chronos_fixture_parses(tmp_path: Path):
    assert FIXTURE_PATH.exists(), (
        f"Missing chronos fixture at {FIXTURE_PATH}; copy it from "
        f"upload_files_for_test/zeppelin-chronos-tags.json."
    )
    bundle = build_app_inspector_bundle(FIXTURE_PATH, ANCHOR_REPO)

    tags = bundle["app_tags"]
    # The canonical sample has ~14k concerns; assert well past the
    # plan's ≥ 100 floor to catch silent shape regressions.
    assert len(tags) >= 100

    project = bundle["project"]
    assert isinstance(project, AppInspectorProject)
    assert project.id == ANCHOR_REPO

    # Spot-check the first row from the fixture: per inspection it is
    # (file/src/main/java/org/apache/zeppelin/file/FileInterpreter.java,
    #  appinspector.OS.Network.Connection.Socket, 18) — i.e. the
    # repo-prefixed entity stripped of the leading "zeppelin/" segment.
    spot_tag = "appinspector.OS.Network.Connection.Socket"
    spot_file = (
        "file/src/main/java/org/apache/zeppelin/file/FileInterpreter.java"
    )
    spot_id = AppTag.make_id(ANCHOR_REPO, spot_file, spot_tag)
    matched = [t for t in tags if t.id == spot_id]
    assert matched, (
        f"Expected a tag with id {spot_id!r} in the chronos fixture."
    )
    spot = matched[0]
    assert spot.tag == spot_tag
    assert spot.file_path == spot_file
    assert spot.strength == 18
    assert spot.file_ref == EntityRef(
        kind=EntityKind.FILE,
        id=File.make_id(ANCHOR_REPO, spot_file),
    )

    # Every fixture row is repo-prefixed, so the meta flag should be True.
    assert bundle["_meta"]["all_rows_self_repo"] is True


# ---------------------------------------------------------------------------
# Test 3 — repo-prefix stripping
# ---------------------------------------------------------------------------


def test_repo_prefix_is_stripped_from_file_path(tmp_path: Path):
    payload = _wrap(
        [
            {
                "entity": f"{ANCHOR_REPO}/src/Foo.java",
                "tag": "appinspector.OS.Network.Connection.Socket",
                "strength": 5,
            }
        ]
    )
    bundle = build_app_inspector_bundle(
        _write_payload(tmp_path, payload), ANCHOR_REPO
    )
    tag = bundle["app_tags"][0]
    assert tag.file_path == "src/Foo.java"
    assert not tag.file_path.startswith(f"{ANCHOR_REPO}/")
    assert tag.file_ref.id == File.make_id(ANCHOR_REPO, "src/Foo.java")


def test_non_prefixed_entity_kept_verbatim_and_flagged(tmp_path: Path):
    """Entities not starting with ``<repo_name>/`` are kept as-is, and
    the ``all_rows_self_repo`` meta flag flips to False."""
    payload = _wrap(
        [
            {
                # No "zeppelin/" prefix on this row.
                "entity": "src/Foo.java",
                "tag": "appinspector.OS.Network.Connection.Socket",
                "strength": 2,
            },
            {
                "entity": f"{ANCHOR_REPO}/src/Bar.java",
                "tag": "appinspector.Cryptography.CryptoCurrency",
                "strength": 1,
            },
        ]
    )
    bundle = build_app_inspector_bundle(
        _write_payload(tmp_path, payload), ANCHOR_REPO
    )
    paths = {t.file_path for t in bundle["app_tags"]}
    assert "src/Foo.java" in paths
    assert "src/Bar.java" in paths
    assert bundle["_meta"]["all_rows_self_repo"] is False


# ---------------------------------------------------------------------------
# Test 4 — malformed-row handling (mirrors quality bridge semantics)
# ---------------------------------------------------------------------------


def test_missing_tag_raises(tmp_path: Path):
    payload = _wrap(
        [{"entity": f"{ANCHOR_REPO}/src/Foo.java", "strength": 1}]
    )
    with pytest.raises(ValueError, match="missing required keys"):
        build_app_inspector_bundle(
            _write_payload(tmp_path, payload), ANCHOR_REPO
        )


def test_missing_entity_raises(tmp_path: Path):
    payload = _wrap(
        [{"tag": "appinspector.OS.X", "strength": 1}]
    )
    with pytest.raises(ValueError, match="missing required keys"):
        build_app_inspector_bundle(
            _write_payload(tmp_path, payload), ANCHOR_REPO
        )


def test_non_int_strength_coerced_to_default(tmp_path: Path):
    """A non-int ``strength`` falls back to 1 (mirroring quality
    bridge's defensive coercion of ``value``)."""
    payload = _wrap(
        [
            {
                "entity": f"{ANCHOR_REPO}/src/Foo.java",
                "tag": "appinspector.OS.Network.Connection.Socket",
                "strength": "not-a-number",
            }
        ]
    )
    bundle = build_app_inspector_bundle(
        _write_payload(tmp_path, payload), ANCHOR_REPO
    )
    assert bundle["app_tags"][0].strength == 1


def test_empty_concerns_list_returns_empty_bundle(tmp_path: Path):
    payload = _wrap([])
    bundle = build_app_inspector_bundle(
        _write_payload(tmp_path, payload), ANCHOR_REPO
    )
    assert bundle["app_tags"] == []
    assert bundle["_meta"]["all_rows_self_repo"] is False


def test_top_level_not_an_object_raises(tmp_path: Path):
    out = tmp_path / "broken.json"
    out.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError, match="top-level"):
        build_app_inspector_bundle(out, ANCHOR_REPO)


def test_missing_file_concerns_raises(tmp_path: Path):
    out = tmp_path / "broken.json"
    out.write_text(json.dumps({"file": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="concerns"):
        build_app_inspector_bundle(out, ANCHOR_REPO)
