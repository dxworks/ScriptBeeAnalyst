"""Tests for the DuDe -> v2 duplication bridge.

Covers the per-row optional ``repo_name`` (internal JSON) and
``repo_name_a`` / ``repo_name_b`` (external CSV) introduced by the
Step-4 follow-up. When a row carries its own anchor it wins; otherwise
the bridge falls back to the function-level ``repo_name`` argument.
"""
from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent
from typing import Any, List

from src.common.domains.duplication.bridge import build_duplication_bundle
from src.common.domains.duplication.models import DuplicationKind
from src.common.domains.git.models import File


ANCHOR_REPO = "zeppelin"


def _write_external(tmp_path: Path, body: str, name: str = "external.csv") -> Path:
    out = tmp_path / name
    out.write_text(dedent(body).lstrip("\n"), encoding="utf-8")
    return out


def _write_internal(tmp_path: Path, payload: List[dict]) -> Path:
    out = tmp_path / "internal.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


def _pair_ids(bundle) -> set[tuple[str, str]]:
    return {
        (p.file_a_ref.id, p.file_b_ref.id) for p in bundle["duplication_pairs"]
    }


# ---------------------------------------------------------------------------
# External CSV
# ---------------------------------------------------------------------------


def test_external_all_rows_self_describe(tmp_path: Path):
    csv = _write_external(
        tmp_path,
        """
        file_a_path,file_b_path,total_block_length,repo_name_a,repo_name_b
        src/A.java,src/B.java,42,other,other
        src/C.java,src/D.java,17,zeppelin,zeppelin
        """,
    )
    bundle = build_duplication_bundle(csv, None, ANCHOR_REPO)
    ids = _pair_ids(bundle)
    expected = {
        tuple(sorted([File.make_id("other", "src/A.java"), File.make_id("other", "src/B.java")])),
        tuple(sorted([File.make_id("zeppelin", "src/C.java"), File.make_id("zeppelin", "src/D.java")])),
    }
    assert ids == expected
    assert bundle["_meta"]["all_rows_self_repo"] is True


def test_external_mixed_rows_fallback(tmp_path: Path):
    csv = _write_external(
        tmp_path,
        """
        file_a_path,file_b_path,total_block_length,repo_name_a,repo_name_b
        src/A.java,src/B.java,42,other,other
        src/C.java,src/D.java,17,,
        """,
    )
    bundle = build_duplication_bundle(csv, None, ANCHOR_REPO)
    ids = _pair_ids(bundle)
    assert tuple(sorted([
        File.make_id("other", "src/A.java"),
        File.make_id("other", "src/B.java"),
    ])) in ids
    # Second row had no self-repo -> bound to anchor.
    assert tuple(sorted([
        File.make_id("zeppelin", "src/C.java"),
        File.make_id("zeppelin", "src/D.java"),
    ])) in ids
    assert bundle["_meta"]["all_rows_self_repo"] is False


def test_external_headerless_full_fallback(tmp_path: Path):
    # Legacy header-less CSV shape — every row falls back to the anchor
    # (preserves pre-change single-repo behaviour).
    csv = _write_external(
        tmp_path,
        """
        src/A.java,src/B.java,42
        src/C.java,src/D.java,17
        """,
    )
    bundle = build_duplication_bundle(csv, None, ANCHOR_REPO)
    ids = _pair_ids(bundle)
    assert tuple(sorted([
        File.make_id("zeppelin", "src/A.java"),
        File.make_id("zeppelin", "src/B.java"),
    ])) in ids
    assert bundle["_meta"]["all_rows_self_repo"] is False


# ---------------------------------------------------------------------------
# Internal JSON
# ---------------------------------------------------------------------------


def test_internal_all_rows_self_describe(tmp_path: Path):
    payload = [
        {"file": "src/A.java", "name": "Internal", "category": "Duplication",
         "value": 10, "repo_name": "other"},
        {"file": "src/B.java", "name": "Internal", "category": "Duplication",
         "value": 8, "repo_name": "zeppelin"},
    ]
    js = _write_internal(tmp_path, payload)
    bundle = build_duplication_bundle(None, js, ANCHOR_REPO)
    pair_ids = {p.file_a_ref.id for p in bundle["duplication_pairs"]}
    assert pair_ids == {
        File.make_id("other", "src/A.java"),
        File.make_id("zeppelin", "src/B.java"),
    }
    # Sanity: all internal pairs are self-pairs.
    for p in bundle["duplication_pairs"]:
        assert p.file_a_ref == p.file_b_ref
        assert p.duplication_kind == DuplicationKind.INTERNAL
    assert bundle["_meta"]["all_rows_self_repo"] is True


def test_internal_mixed_rows_fallback(tmp_path: Path):
    payload = [
        {"file": "src/A.java", "name": "Internal", "category": "Duplication",
         "value": 10, "repo_name": "other"},
        # No repo_name -> fall back.
        {"file": "src/B.java", "name": "Internal", "category": "Duplication",
         "value": 8},
    ]
    js = _write_internal(tmp_path, payload)
    bundle = build_duplication_bundle(None, js, ANCHOR_REPO)
    pair_ids = {p.file_a_ref.id for p in bundle["duplication_pairs"]}
    assert File.make_id("other", "src/A.java") in pair_ids
    assert File.make_id(ANCHOR_REPO, "src/B.java") in pair_ids
    assert bundle["_meta"]["all_rows_self_repo"] is False


def test_internal_full_fallback_no_repo_field(tmp_path: Path):
    payload = [
        {"file": "src/A.java", "name": "Internal", "category": "Duplication",
         "value": 10},
        {"file": "src/B.java", "name": "Internal", "category": "Duplication",
         "value": 8},
    ]
    js = _write_internal(tmp_path, payload)
    bundle = build_duplication_bundle(None, js, ANCHOR_REPO)
    pair_ids = {p.file_a_ref.id for p in bundle["duplication_pairs"]}
    assert pair_ids == {
        File.make_id(ANCHOR_REPO, "src/A.java"),
        File.make_id(ANCHOR_REPO, "src/B.java"),
    }
    assert bundle["_meta"]["all_rows_self_repo"] is False


def test_combined_external_and_internal_meta_intersects(tmp_path: Path):
    # External fully self-describes, internal does not -> overall not all.
    csv = _write_external(
        tmp_path,
        """
        file_a_path,file_b_path,total_block_length,repo_name_a,repo_name_b
        src/A.java,src/B.java,42,other,other
        """,
    )
    payload: List[dict[str, Any]] = [
        {"file": "src/C.java", "name": "Internal", "category": "Duplication",
         "value": 9},
    ]
    js = _write_internal(tmp_path, payload)
    bundle = build_duplication_bundle(csv, js, ANCHOR_REPO)
    assert bundle["_meta"]["all_rows_self_repo"] is False
