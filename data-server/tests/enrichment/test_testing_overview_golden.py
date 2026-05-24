"""Golden snapshot test for the Chunk-18 :class:`TestingTableBuilder`.

Approach mirrors :file:`test_authorship_overview_golden.py`: a
deterministic v2 fixture (fixed anchor + seeded commits per file) feeds
the builder; the serialised :class:`OverviewTable` is locked in
``_testing_overview_golden.json``.

If a downstream change moves a cell intentionally, regenerate the
snapshot via the ``__main__`` helper at the bottom of this file and
commit the new JSON.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.enrichment.config import EnrichmentConfig
from src.enrichment.overviews import OVERVIEWS
from src.enrichment.overviews.implementations.testing_table import (
    COLUMNS,
    TestingTableBuilder,
)
from src.enrichment.pipeline import run_pipeline

from tests.enrichment.conftest import (
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)


UTC = timezone.utc
_GOLDEN_PATH = (
    Path(__file__).parent / "_testing_overview_golden.json"
)


# ----------------------------------------------------------------------
# Fixture — deterministic ``Graph`` for the golden assertion.
# ----------------------------------------------------------------------
def _build_golden_graph():
    """Deterministic v2 graph engineered to exercise every testing column.

    Layout:

    * ``src/bug.py`` — Alice 6 bugfix commits → drives the ``bugfix``
      ratio on ``src/`` and feeds the BugMagnet metric's
      bugfix-share-per-file threshold.
    * ``src/feature.py`` — Alice 2 feature commits (denominator for
      the bugfix ratio).
    * ``tests/test_bug.py`` — Bob 2 test commits; classified as
      ``role="test"`` by :class:`FileClassifierMetric` so
      ``test_file_ratio`` / ``test_to_prod_ratio`` carry a value.
    """
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    graph, project = build_v2_graph("testing-golden")
    graph.__dict__["recent_cutoff"] = anchor - timedelta(days=90)

    alice = make_account("Alice", "a@x", project.ref())
    bob = make_account("Bob", "b@x", project.ref())
    graph.git_accounts.add(alice)
    graph.git_accounts.add(bob)

    bug = make_file("src/bug.py", project.ref())
    feature = make_file("src/feature.py", project.ref())
    test_bug = make_file("tests/test_bug.py", project.ref())
    for f in (bug, feature, test_bug):
        graph.files.add(f)

    # src/bug.py — 6 bugfix-message commits by Alice.
    for i in range(6):
        c = make_commit(
            f"bug_{i}", "fix: edge case", alice,
            anchor - timedelta(days=10 + i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, bug, added=8, deleted=2)

    # src/feature.py — 2 feature commits by Alice.
    for i in range(2):
        c = make_commit(
            f"feat_{i}", "feat: shiny", alice,
            anchor - timedelta(days=20 + i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, feature, added=30, deleted=5)

    # tests/test_bug.py — 2 test commits by Bob. Message intentionally
    # avoids the bugfix-pattern keywords so message.nature stays "test".
    for i in range(2):
        c = make_commit(
            f"test_{i}", "test: add coverage", bob,
            anchor - timedelta(days=8 + i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, test_bug, added=15, deleted=0)

    cfg = EnrichmentConfig(bugmagnet_min_bugfix_commits=5)
    graph.__dict__["config"] = cfg
    run_pipeline(graph, cfg)
    return graph


# ----------------------------------------------------------------------
# Sanity (shape) assertions
# ----------------------------------------------------------------------
def test_testing_overview_registered():
    assert "testing" in OVERVIEWS.names()
    assert OVERVIEWS.get("testing") is TestingTableBuilder


def test_testing_overview_columns_match_spec():
    graph = _build_golden_graph()
    table = TestingTableBuilder().build(graph, EnrichmentConfig())
    assert table.columns == COLUMNS
    assert table.entity_kind == "component"


def test_testing_overview_row_ids_match_fixture():
    graph = _build_golden_graph()
    table = TestingTableBuilder().build(graph, EnrichmentConfig())
    row_ids = [r.entity_id for r in table.rows]
    # Synthetic ``(project)`` aggregate + one per top-level folder.
    assert row_ids == ["(project)", "src", "tests"]


def test_testing_overview_known_cell_values():
    """Specific cell values from the engineered fixture."""
    graph = _build_golden_graph()
    table = TestingTableBuilder().build(graph, EnrichmentConfig())
    rows = {r.entity_id: r for r in table.rows}

    project = rows["(project)"]
    # 1 of 3 files is test-classified → 1/3 ≈ 0.3333.
    assert project.cells["test_file_ratio"].lifetime_value == round(1 / 3, 4)
    # 6 bugfix commits / 10 total → 0.6.
    assert project.cells["bugfix_commit_ratio"].lifetime_value == 0.6
    # tests/prod = 1/2 = 0.5.
    assert project.cells["test_to_prod_ratio"].lifetime_value == 0.5
    # bugmagnet_files is the count of files carrying the trait — may be
    # 0 or 1 depending on the per-file threshold; check it's an int.
    assert isinstance(project.cells["bugmagnet_files"].lifetime_value, int)


# ----------------------------------------------------------------------
# Locked snapshot — the golden assertion.
# ----------------------------------------------------------------------
def test_testing_overview_matches_locked_golden():
    """Field-for-field comparison against the locked snapshot."""
    if not _GOLDEN_PATH.is_file():
        pytest.skip(
            f"missing golden at {_GOLDEN_PATH}; regenerate via "
            f"`python tests/enrichment/test_testing_overview_golden.py`"
        )
    expected = json.loads(_GOLDEN_PATH.read_text())

    graph = _build_golden_graph()
    table = TestingTableBuilder().build(graph, EnrichmentConfig())
    actual = table.model_dump(mode="json")

    assert actual == expected


# ----------------------------------------------------------------------
# Regeneration helper — runs the fixture and writes the golden.
# ----------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    graph = _build_golden_graph()
    table = TestingTableBuilder().build(graph, EnrichmentConfig())
    _GOLDEN_PATH.write_text(
        json.dumps(table.model_dump(mode="json"), indent=2, sort_keys=True)
        + "\n"
    )
    print(f"wrote golden to {_GOLDEN_PATH}")
