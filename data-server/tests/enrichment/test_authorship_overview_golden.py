"""Golden CSV-shape test for the Authorship overview on the synthetic fixture.

We assert structure (header, row identifiers, key cell values) rather than the
full CSV byte-for-byte, since trend percentages depend on small floats that
shift across Python versions. Header layout and per-(project) values are stable.
"""
from __future__ import annotations

from src.enrichment.config import EnrichmentConfig
from src.enrichment.overview.writer import to_csv_bytes
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import build_synthetic_graph


def test_authorship_overview_csv_shape_and_known_values():
    g = build_synthetic_graph()
    e = compute_enrichments(g, EnrichmentConfig())
    table = e.overview("authorship")
    assert table is not None

    # Sanity: rows include the synthetic project aggregate and per-folder rows.
    row_ids = [r.entity_id for r in table.rows]
    assert "(project)" in row_ids
    # Top-level folder for our fixture is `src` (all files live under src/).
    assert "src" in row_ids

    # Render to CSV and verify header is one entity_id column + 3 columns/spec.
    csv_bytes = to_csv_bytes(table)
    csv = csv_bytes.decode("utf-8")
    header_line = csv.splitlines()[0]
    cols = header_line.split(",")
    assert cols[0] == "entity_id"
    expected_cols = (
        ["entity_id"]
        + [f"{name}_{suffix}"
           for name in table.columns
           for suffix in ("lifetime", "recent", "trend_percent")]
    )
    assert cols == expected_cols

    # Project row: 2 distinct authors, BusFactor1 should flag at least owner.py.
    project_row = next(r for r in table.rows if r.entity_id == "(project)")
    assert project_row.cells["total_authors"].lifetime_value == 2
    assert project_row.cells["bus_factor_1_files"].lifetime_value >= 1
    # Dominant share is between 0 and 1.
    dominant = project_row.cells["dominant_author_share"].lifetime_value
    assert dominant is not None and 0.0 <= dominant <= 1.0
