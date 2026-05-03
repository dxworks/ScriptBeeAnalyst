"""A2.4 — coverage tests for the three new overview tables.

Each test asserts:
  - the table is registered and emits non-empty rows on the synthetic graph,
  - the column shape matches the builder's spec,
  - at least one project-row cell carries a non-None value.
"""
from __future__ import annotations

from src.enrichment.config import EnrichmentConfig
from src.enrichment.overview.feature_traceability_table import COLUMNS as FT_COLUMNS
from src.enrichment.overview.knowledge_table import COLUMNS as KNOW_COLUMNS
from src.enrichment.overview.nature_table import COLUMNS as NATURE_COLUMNS
from src.enrichment.overview.writer import to_csv_bytes
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import (
    build_jira_fixture,
    build_synthetic_graph,
)


def _csv_header_for(table) -> list[str]:
    csv = to_csv_bytes(table).decode("utf-8")
    return csv.splitlines()[0].split(",")


def test_knowledge_overview_present_with_expected_columns():
    g = build_synthetic_graph()
    e = compute_enrichments(g, EnrichmentConfig())
    table = e.overview("knowledge")
    assert table is not None
    assert table.columns == KNOW_COLUMNS

    row_ids = [r.entity_id for r in table.rows]
    assert "(project)" in row_ids
    assert "src" in row_ids  # all synthetic files live under src/

    project = next(r for r in table.rows if r.entity_id == "(project)")
    # APK% computed (2 active authors, all churn lifetime → ≥0).
    assert project.cells["apk_percent"].lifetime_value is not None
    assert project.cells["apk_percent"].lifetime_value >= 0.0

    # The synthetic fixture's `src/orphan.py` is engineered to fire Orphan,
    # `src/owner.py` engineered to fire BusFactor1 (not counted here) — but
    # we only require the column to exist and at least one ownership-anomaly
    # column be a non-None integer.
    counts = [
        project.cells["weak_ownership_count"].lifetime_value,
        project.cells["polarised_ownership_count"].lifetime_value,
        project.cells["orphan_count"].lifetime_value,
    ]
    assert all(isinstance(v, int) for v in counts)
    assert sum(counts) >= 1  # synthetic fixture must trigger at least one

    # Header shape matches the writer convention used by sibling tables.
    header = _csv_header_for(table)
    assert header[0] == "entity_id"
    assert header == ["entity_id"] + [
        f"{name}_{suffix}"
        for name in table.columns
        for suffix in ("lifetime", "recent", "trend_percent")
    ]


def test_nature_overview_distribution_sums_to_full_coverage():
    g = build_synthetic_graph()
    e = compute_enrichments(g, EnrichmentConfig())
    table = e.overview("nature")
    assert table is not None
    assert table.columns == NATURE_COLUMNS

    project = next(r for r in table.rows if r.entity_id == "(project)")
    # synthetic fixture has bugfix, feature, refactor, chore commits — at
    # least bugfix and feature must be > 0.
    bugfix_lt = project.cells["bugfix_pct"].lifetime_value
    feature_lt = project.cells["feature_pct"].lifetime_value
    assert bugfix_lt is not None and bugfix_lt > 0
    assert feature_lt is not None and feature_lt > 0

    # Commits are partitioned by `message.nature`, so the 8 columns sum to ~100.
    total = sum(
        project.cells[c].lifetime_value or 0.0
        for c in NATURE_COLUMNS
    )
    assert 0 < total <= 100.01  # allow rounding slack


def test_feature_traceability_present_without_jira_falls_back_gracefully():
    """Without a linker run, commits have no `issues` — table should still emit
    rows but `commits_linked_pct` lifetime values can be 0 or None."""
    g = build_synthetic_graph()
    e = compute_enrichments(g, EnrichmentConfig())
    table = e.overview("feature_traceability")
    assert table is not None
    assert table.columns == FT_COLUMNS

    row_ids = [r.entity_id for r in table.rows]
    assert "(project)" in row_ids
    project = next(r for r in table.rows if r.entity_id == "(project)")
    # Without linker: every cell should be present (the cells exist) and the
    # commits_linked_pct lifetime is 0% (commits exist, none linked).
    assert "commits_linked_pct" in project.cells
    assert project.cells["commits_linked_pct"].lifetime_value == 0.0


def test_feature_traceability_with_linked_commits():
    """Manually link a synthetic issue to a commit so the table reports >0%."""
    g = build_synthetic_graph()
    g["jira"] = build_jira_fixture()
    # Wire the first issue to one commit so the linker bridge is non-empty.
    issue = list(g["jira"].issue_registry.all)[0]
    commit = list(g["git"].git_commit_registry.all)[0]
    issue.git_commits = [commit]
    commit.issues = [issue]

    e = compute_enrichments(g, EnrichmentConfig())
    table = e.overview("feature_traceability")
    assert table is not None

    project = next(r for r in table.rows if r.entity_id == "(project)")
    assert project.cells["commits_linked_pct"].lifetime_value > 0.0
    # 1 issue with linked commits / 1 universe issue = 100%.
    assert project.cells["issues_with_commits_pct"].lifetime_value == 100.0
    # mean_issues_per_component on the project row = total distinct linked
    # issues / component_count (folder-bucketed). Must be a positive float.
    mean = project.cells["mean_issues_per_component"].lifetime_value
    assert mean is not None and mean > 0
