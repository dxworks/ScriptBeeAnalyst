"""DynamicBlob anomaly + components-overview LOC columns (B1 Lizard).

Verifies §10 of communication/B1_lizard/index_step_general.md.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.common.lizard_models import FileMetric, FunctionMetric
from src.common.models import GitProject
from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import (
    make_account,
    make_change,
    make_commit,
    make_file,
)


UTC = timezone.utc


def _trait(tags, name):
    return next((t for t in tags.traits if t.name == name), None)


def _make_metric(file_path: str, nloc: int, ccn: int, fns: int = 5) -> FileMetric:
    return FileMetric(
        file_path=file_path,
        source="lizard",
        sum_nloc=nloc,
        max_ccn=ccn,
        avg_ccn=float(ccn),
        function_count=fns,
        longest_function_nloc=nloc // max(fns, 1),
        functions=[
            FunctionMetric(
                name=f"fn_{i}",
                long_name=f"fn_{i}()",
                class_name=None,
                nloc=nloc // max(fns, 1),
                cyclomatic_complexity=ccn,
                parameters=0,
                token_count=10,
                length=nloc // max(fns, 1) + 2,
                start_line=i * 10 + 1,
                end_line=i * 10 + 9,
            )
            for i in range(fns)
        ],
    )


def _build_graph_with_lizard(file_path: str, n_commits: int, nloc: int, ccn: int = 8):
    now = datetime.now(UTC)
    proj = GitProject(name="b1-lizard")
    alice = make_account("Alice", "alice@example.com")
    proj.account_registry.add_all([alice])
    fobj = make_file(proj)
    proj.file_registry.add_all([fobj])

    for i in range(n_commits):
        c = make_commit(proj, f"c_{i}", f"feat: edit {i}", alice,
                        now - timedelta(days=10 + i))
        make_change(c, fobj, file_path, added=10, deleted=2)
        proj.git_commit_registry.add(c)

    metric = _make_metric(file_path, nloc=nloc, ccn=ccn)
    return {
        "git": proj,
        "jira": None,
        "github": None,
        "metrics": {"lizard": [metric]},
    }


def test_dynamicblob_fires_on_high_loc_high_churn():
    g = _build_graph_with_lizard("src/big.py", n_commits=30, nloc=2000, ccn=15)
    e = compute_enrichments(g, EnrichmentConfig())
    tags = e.tags_by_entity.get("file:src/big.py")
    assert tags is not None
    db = _trait(tags, "anomaly.cohesion.size.DynamicBlob")
    assert db is not None
    assert db.evidence["loc"] == 2000
    assert db.evidence["changes"] == 30
    assert db.evidence["threshold_loc"] == 500
    assert db.evidence["threshold_changes"] == 20
    assert db.severity >= 1
    assert db.severity <= 10


def test_dynamicblob_skipped_below_loc_threshold():
    g = _build_graph_with_lizard("src/small.py", n_commits=30, nloc=100)
    e = compute_enrichments(g, EnrichmentConfig())
    tags = e.tags_by_entity.get("file:src/small.py")
    if tags is not None:
        assert _trait(tags, "anomaly.cohesion.size.DynamicBlob") is None


def test_dynamicblob_skipped_below_change_threshold():
    g = _build_graph_with_lizard("src/big_quiet.py", n_commits=5, nloc=2000)
    e = compute_enrichments(g, EnrichmentConfig())
    tags = e.tags_by_entity.get("file:src/big_quiet.py")
    if tags is not None:
        assert _trait(tags, "anomaly.cohesion.size.DynamicBlob") is None


def test_dynamicblob_skipped_when_no_lizard_metric():
    """File present in git but no Lizard rollup → no DynamicBlob (graceful)."""
    g = _build_graph_with_lizard("src/known.py", n_commits=30, nloc=2000)
    g["metrics"] = {"lizard": []}
    e = compute_enrichments(g, EnrichmentConfig())
    tags = e.tags_by_entity.get("file:src/known.py")
    if tags is not None:
        assert _trait(tags, "anomaly.cohesion.size.DynamicBlob") is None


def test_components_overview_includes_loc_columns():
    g = _build_graph_with_lizard("src/big.py", n_commits=30, nloc=2000, ccn=15)
    e = compute_enrichments(g, EnrichmentConfig())
    table = e.overview("components")
    assert table is not None
    assert "total_loc" in table.columns
    assert "avg_loc_per_file" in table.columns
    assert "max_ccn" in table.columns
    project_row = next(r for r in table.rows if r.entity_id == "(project)")
    assert project_row.cells["total_loc"].lifetime_value == 2000
    assert project_row.cells["max_ccn"].lifetime_value == 15
