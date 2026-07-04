"""Tests for the Lizard CSV -> v2 entity-bundle bridge.

Covers the per-row optional ``repo_name`` column added by the Step-4
follow-up: when a row carries its own ``repo_name`` it wins; otherwise
the bridge falls back to the function-level ``repo_name`` anchor. The
bundle's ``_meta["all_rows_self_repo"]`` flag reports whether every
parsed row was self-described.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from src.common.domains.git.models import File
from src.common.domains.metrics_lizard.bridge import build_lizard_bundle


ANCHOR_REPO = "zeppelin"
HEADER = "NLOC,CCN,token,PARAM,length,location,file,function,long_name,start,end"
HEADER_WITH_REPO = HEADER + ",repo_name"


def _write_csv(tmp_path: Path, body: str) -> Path:
    out = tmp_path / "lizard.csv"
    out.write_text(dedent(body).lstrip("\n"), encoding="utf-8")
    return out


def _file_metric_ids(bundle) -> set[str]:
    return {m.file_ref.id for m in bundle["file_metrics"]}


def test_all_rows_carry_repo_name(tmp_path: Path):
    csv = _write_csv(
        tmp_path,
        f"""
        {HEADER_WITH_REPO}
        10,2,40,1,12,loc,/repos/zeppelin/src/A.java,run,A::run(),1,12,zeppelin
        8,3,30,0,9,loc,/repos/other/src/B.java,run,B::run(),1,9,other
        """,
    )
    bundle = build_lizard_bundle(csv, ANCHOR_REPO)

    ids = _file_metric_ids(bundle)
    assert File.make_id("zeppelin", "src/A.java") in ids
    assert File.make_id("other", "src/B.java") in ids
    # Anchor's view of B.java must NOT be present.
    assert File.make_id("zeppelin", "src/B.java") not in ids
    assert bundle["_meta"]["all_rows_self_repo"] is True


def test_mixed_rows_partial_repo_name(tmp_path: Path):
    # First row carries its own repo_name; second leaves it blank ->
    # fallback to the anchor.
    csv = _write_csv(
        tmp_path,
        f"""
        {HEADER_WITH_REPO}
        10,2,40,1,12,loc,/repos/other/src/A.java,run,A::run(),1,12,other
        8,3,30,0,9,loc,/repos/zeppelin/src/B.java,run,B::run(),1,9,
        """,
    )
    bundle = build_lizard_bundle(csv, ANCHOR_REPO)

    ids = _file_metric_ids(bundle)
    assert File.make_id("other", "src/A.java") in ids
    # Second row had no self-repo -> bound to anchor.
    assert File.make_id("zeppelin", "src/B.java") in ids
    assert bundle["_meta"]["all_rows_self_repo"] is False


def test_no_rows_carry_repo_name_full_fallback(tmp_path: Path):
    # No repo_name column at all -> pre-change behaviour preserved.
    csv = _write_csv(
        tmp_path,
        f"""
        {HEADER}
        10,2,40,1,12,loc,/repos/zeppelin/zeppelin/src/A.java,run,A::run(),1,12
        8,3,30,0,9,loc,/repos/zeppelin/zeppelin/src/B.java,run,B::run(),1,9
        """,
    )
    bundle = build_lizard_bundle(csv, ANCHOR_REPO)

    ids = _file_metric_ids(bundle)
    assert ids == {
        File.make_id("zeppelin", "src/A.java"),
        File.make_id("zeppelin", "src/B.java"),
    }
    assert bundle["_meta"]["all_rows_self_repo"] is False


def test_empty_file_after_header_reports_not_self_described(tmp_path: Path):
    # No data rows -> meta defaults to False (nothing to certify).
    csv = _write_csv(tmp_path, f"\n{HEADER_WITH_REPO}\n")
    bundle = build_lizard_bundle(csv, ANCHOR_REPO)
    assert bundle["file_metrics"] == []
    assert bundle["_meta"]["all_rows_self_repo"] is False
