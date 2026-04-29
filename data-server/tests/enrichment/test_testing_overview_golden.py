"""Golden CSV-shape test for the Testing overview."""
from __future__ import annotations

from src.enrichment.config import EnrichmentConfig
from src.enrichment.overview.writer import to_csv_bytes
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import build_synthetic_graph


def test_testing_overview_csv_shape_and_known_values():
    g = build_synthetic_graph()
    e = compute_enrichments(g, EnrichmentConfig())
    table = e.overview("testing")
    assert table is not None

    row_ids = [r.entity_id for r in table.rows]
    assert "(project)" in row_ids
    assert "src" in row_ids

    csv = to_csv_bytes(table).decode("utf-8")
    header = csv.splitlines()[0].split(",")
    assert header[0] == "entity_id"
    expected_cols = (
        ["entity_id"]
        + [f"{name}_{suffix}"
           for name in table.columns
           for suffix in ("lifetime", "recent", "trend_percent")]
    )
    assert header == expected_cols

    project_row = next(r for r in table.rows if r.entity_id == "(project)")
    # Synthetic fixture has 1 BugMagnet file (src/buggy.py).
    assert project_row.cells["bugmagnet_files"].lifetime_value == 1
    # No test-role files in the fixture, so test_file_ratio is 0.0.
    assert project_row.cells["test_file_ratio"].lifetime_value == 0.0
    # bugfix_commit_ratio is between 0 and 1.
    bf = project_row.cells["bugfix_commit_ratio"].lifetime_value
    assert bf is not None and 0.0 <= bf <= 1.0
