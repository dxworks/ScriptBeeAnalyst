"""Components overview: golden CSV shape on a 2-component fixture."""
from __future__ import annotations

import json
import tempfile

from src.enrichment.config import EnrichmentConfig
from src.enrichment.overview.writer import to_csv_bytes
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import build_synthetic_graph


def _two_component_mapping_path() -> str:
    payload = {
        "buggy": {"path_prefix": "src/buggy"},
        "owner": {"path_prefix": "src/owner"},
    }
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(payload, fh)
    fh.close()
    return fh.name


def test_components_overview_two_component_fixture():
    g = build_synthetic_graph()
    cfg = EnrichmentConfig(components_mapping_path=_two_component_mapping_path())
    e = compute_enrichments(g, cfg)
    table = e.overview("components")
    assert table is not None

    row_ids = [r.entity_id for r in table.rows]
    assert "(project)" in row_ids
    assert "buggy" in row_ids
    assert "owner" in row_ids

    # CSV header: 1 entity_id col + (3 cols * len(columns))
    csv = to_csv_bytes(table).decode("utf-8")
    header = csv.splitlines()[0].split(",")
    assert header[0] == "entity_id"
    expected = ["entity_id"] + [
        f"{name}_{suffix}"
        for name in table.columns
        for suffix in ("lifetime", "recent", "trend_percent")
    ]
    assert header == expected

    by_id = {r.entity_id: r for r in table.rows}
    buggy = by_id["buggy"]
    owner = by_id["owner"]

    # buggy: 6 commits in fixture
    assert buggy.cells["file_count"].lifetime_value == 1
    assert buggy.cells["commit_count"].lifetime_value == 6
    # owner: BugFactor1 should fire on src/owner.py from the synthetic fixture.
    assert owner.cells["bus_factor_1_files"].lifetime_value >= 1
    # Bugfix ratio for buggy is 5/6 -> ~0.833
    assert buggy.cells["bugfix_ratio"].lifetime_value is not None
    assert buggy.cells["bugfix_ratio"].lifetime_value >= 0.5

    project = by_id["(project)"]
    assert project.cells["file_count"].lifetime_value == 3
