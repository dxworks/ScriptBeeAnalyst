"""Intent / Impact overview: column shape + at least one non-zero cell."""
from __future__ import annotations

from src.enrichment.config import EnrichmentConfig
from src.enrichment.overview.writer import to_csv_bytes
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import (
    build_jira_fixture,
    build_synthetic_graph,
)


def test_intent_impact_table_is_present_with_jira():
    g = build_synthetic_graph()
    g["jira"] = build_jira_fixture()
    e = compute_enrichments(g, EnrichmentConfig())
    table = e.overview("intent_impact")
    assert table is not None

    row_ids = [r.entity_id for r in table.rows]
    assert "(project)" in row_ids
    # Bug type from the fixture.
    assert "Bug" in row_ids

    project_row = next(r for r in table.rows if r.entity_id == "(project)")
    # 2 issues in the synthetic Jira fixture.
    assert project_row.cells["issue_count"].lifetime_value == 2

    csv = to_csv_bytes(table).decode("utf-8")
    header = csv.splitlines()[0].split(",")
    assert header[0] == "entity_id"
    expected = ["entity_id"] + [
        f"{name}_{suffix}"
        for name in table.columns
        for suffix in ("lifetime", "recent", "trend_percent")
    ]
    assert header == expected


def test_intent_impact_empty_without_jira():
    g = build_synthetic_graph()
    e = compute_enrichments(g, EnrichmentConfig())
    table = e.overview("intent_impact")
    assert table is not None
    assert table.rows == []
