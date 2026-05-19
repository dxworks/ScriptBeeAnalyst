"""Dispatcher behaviour for the per-row ``repo_name`` follow-up.

The dispatcher (``_downloaded_files_to_bundles``) used to log a blanket
"binding to first repo" warning whenever multiple git uploads coexisted
with any dependent artefact. Step 4 makes the warning conditional: it
only fires for bundles that actually fell back to the anchor — bundles
whose every row carried its own ``repo_name`` are silently honoured.

We stub the git bridge with a monkeypatch so tests don't need real
.iglog files; the dispatcher's warning logic is independent of git
bundle contents.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from src.common.domains.git.models import File, GitProject
from src.common.people import SourceKind
from src import processor as processor_module
from src.processor import DownloadedFiles, _downloaded_files_to_bundles


@pytest.fixture(autouse=True)
def _stub_git_bridge(monkeypatch: pytest.MonkeyPatch):
    """Replace the git bridge with a stub that returns a minimal bundle.

    The dispatcher only cares about iterating ``downloaded.git_files``
    and calling the bridge per repo; the bundle contents are opaque to
    the warning path under test.
    """
    def _fake_build_git_bundle(path: Path, repo: str, project_name: str) -> Mapping[str, Any]:
        project = GitProject(id=repo, name=project_name, source=SourceKind.GIT)
        return {
            "project": project,
            "accounts": [],
            "commits": [],
            "files": [],
            "changes": [],
            "hunks": [],
        }
    monkeypatch.setattr(processor_module, "build_git_bundle", _fake_build_git_bundle)


def _write_quality(tmp_path: Path, payload, name="code_smells.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _two_repo_downloaded(tmp_path: Path) -> DownloadedFiles:
    return DownloadedFiles(
        git_files=[
            ("zeppelin", tmp_path / "zeppelin.iglog"),
            ("other", tmp_path / "other.iglog"),
        ]
    )


def test_warning_suppressed_when_quality_fully_self_describes(tmp_path, caplog):
    downloaded = _two_repo_downloaded(tmp_path)
    downloaded.quality_issues_file = _write_quality(
        tmp_path,
        [
            {"name": "Stub", "category": "Inheritance", "file": "src/A.java",
             "value": 2, "repo_name": "other"},
            {"name": "Stub", "category": "Inheritance", "file": "src/B.java",
             "value": 1, "repo_name": "zeppelin"},
        ],
    )

    with caplog.at_level("WARNING"):
        bundles = _downloaded_files_to_bundles(downloaded)

    # Warning must NOT fire — every issue self-described.
    fallback_warnings = [
        rec for rec in caplog.records
        if "rows without a self-describing repo_name" in rec.getMessage()
    ]
    assert fallback_warnings == []

    # Bundle file refs honour the per-row repo, not the anchor.
    quality_bundles = bundles[SourceKind.QUALITY]
    issue_ids = {i.file_ref.id for i in quality_bundles[0]["quality_issues"]}
    assert File.make_id("other", "src/A.java") in issue_ids
    assert File.make_id("zeppelin", "src/B.java") in issue_ids
    # And the dispatcher must have stripped the _meta key so the
    # transformer doesn't choke on an unknown bundle key.
    assert "_meta" not in quality_bundles[0]


def test_warning_emitted_when_quality_mixed(tmp_path, caplog):
    downloaded = _two_repo_downloaded(tmp_path)
    downloaded.quality_issues_file = _write_quality(
        tmp_path,
        [
            {"name": "Stub", "category": "Inheritance", "file": "src/A.java",
             "value": 2, "repo_name": "other"},
            # No repo_name -> bound to the anchor "zeppelin".
            {"name": "Stub", "category": "Inheritance", "file": "src/B.java",
             "value": 1},
        ],
    )

    with caplog.at_level("WARNING"):
        _downloaded_files_to_bundles(downloaded)

    fallback_warnings = [
        rec for rec in caplog.records
        if "rows without a self-describing repo_name" in rec.getMessage()
    ]
    assert len(fallback_warnings) == 1
    msg = fallback_warnings[0].getMessage()
    assert "quality" in msg


def test_warning_emitted_when_quality_fully_falls_back(tmp_path, caplog):
    # Legacy single-repo upload behaviour — no repo_name fields at all
    # but two git uploads exist, so the warning must still fire.
    downloaded = _two_repo_downloaded(tmp_path)
    downloaded.quality_issues_file = _write_quality(
        tmp_path,
        [
            {"name": "Stub", "category": "Inheritance", "file": "src/A.java",
             "value": 2},
        ],
    )

    with caplog.at_level("WARNING"):
        _downloaded_files_to_bundles(downloaded)

    fallback_warnings = [
        rec for rec in caplog.records
        if "rows without a self-describing repo_name" in rec.getMessage()
    ]
    assert len(fallback_warnings) == 1


def test_no_warning_when_only_one_git_repo(tmp_path, caplog):
    # Single-repo upload — even with no per-row repo_name the dispatcher
    # must stay silent (the anchor IS the only repo).
    downloaded = DownloadedFiles(
        git_files=[("zeppelin", tmp_path / "zeppelin.iglog")],
    )
    downloaded.quality_issues_file = _write_quality(
        tmp_path,
        [
            {"name": "Stub", "category": "Inheritance", "file": "src/A.java",
             "value": 2},
        ],
    )

    with caplog.at_level("WARNING"):
        _downloaded_files_to_bundles(downloaded)

    fallback_warnings = [
        rec for rec in caplog.records
        if "rows without a self-describing repo_name" in rec.getMessage()
    ]
    assert fallback_warnings == []


def test_meta_key_stripped_from_dependent_bundle(tmp_path):
    """Transformers reject unknown bundle keys via ``collect_bundle``;
    the dispatcher must pop ``_meta`` from every dependent bundle before
    handing them downstream, regardless of self-repo state.
    """
    downloaded = _two_repo_downloaded(tmp_path)
    downloaded.quality_issues_file = _write_quality(
        tmp_path,
        [{"name": "S", "category": "C", "file": "src/A.java", "value": 1}],
    )

    bundles = _downloaded_files_to_bundles(downloaded)

    for bundle in bundles[SourceKind.QUALITY]:
        assert "_meta" not in bundle
