"""Tests for the Insider -> v2 quality bridge.

Covers the per-row optional ``repo_name`` field introduced by the
Step-4 follow-up. When an issue carries its own ``repo_name`` it wins;
otherwise the bridge falls back to the function-level anchor.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from src.common.domains.git.models import File
from src.common.domains.quality.bridge import build_quality_bundle


ANCHOR_REPO = "zeppelin"


def _write_quality(tmp_path: Path, payload: List[dict]) -> Path:
    out = tmp_path / "code_smells.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


def _issue_file_ids(bundle) -> set[str]:
    return {issue.file_ref.id for issue in bundle["quality_issues"]}


def test_all_issues_carry_repo_name(tmp_path: Path):
    payload = [
        {"name": "Stub", "category": "Inheritance", "file": "src/A.java",
         "value": 2, "repo_name": "other"},
        {"name": "Stub", "category": "Inheritance", "file": "src/B.java",
         "value": 1, "repo_name": "zeppelin"},
    ]
    bundle = build_quality_bundle(_write_quality(tmp_path, payload), ANCHOR_REPO)
    assert _issue_file_ids(bundle) == {
        File.make_id("other", "src/A.java"),
        File.make_id("zeppelin", "src/B.java"),
    }
    assert bundle["_meta"]["all_rows_self_repo"] is True


def test_mixed_issues_partial_repo_name(tmp_path: Path):
    payload = [
        {"name": "Stub", "category": "Inheritance", "file": "src/A.java",
         "value": 2, "repo_name": "other"},
        # No repo_name -> falls back to anchor.
        {"name": "Stub", "category": "Inheritance", "file": "src/B.java",
         "value": 1},
    ]
    bundle = build_quality_bundle(_write_quality(tmp_path, payload), ANCHOR_REPO)
    ids = _issue_file_ids(bundle)
    assert File.make_id("other", "src/A.java") in ids
    assert File.make_id(ANCHOR_REPO, "src/B.java") in ids
    assert bundle["_meta"]["all_rows_self_repo"] is False


def test_no_issues_carry_repo_name_full_fallback(tmp_path: Path):
    payload = [
        {"name": "Stub", "category": "Inheritance", "file": "src/A.java",
         "value": 2},
        {"name": "Stub", "category": "Inheritance", "file": "src/B.java",
         "value": 1},
    ]
    bundle = build_quality_bundle(_write_quality(tmp_path, payload), ANCHOR_REPO)
    assert _issue_file_ids(bundle) == {
        File.make_id(ANCHOR_REPO, "src/A.java"),
        File.make_id(ANCHOR_REPO, "src/B.java"),
    }
    assert bundle["_meta"]["all_rows_self_repo"] is False


def test_empty_payload_reports_not_self_described(tmp_path: Path):
    bundle = build_quality_bundle(_write_quality(tmp_path, []), ANCHOR_REPO)
    assert bundle["quality_issues"] == []
    assert bundle["_meta"]["all_rows_self_repo"] is False
