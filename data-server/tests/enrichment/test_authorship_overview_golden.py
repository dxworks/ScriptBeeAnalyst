"""Golden snapshot test for the Authorship overview on a v2 fixture.

Restored from ``git show f840488^:data-server/tests/enrichment/test_authorship_overview_golden.py``,
then rebuilt against v2 fixture data per the Chunk-17 brief
("Rebuild goldens against v2 fixture data, then lock").

Approach: an end-to-end pipeline run on a deterministic fixture
(anchor = 2026-01-01 UTC, fixed seeds) feeds
:class:`AuthorshipTableBuilder`. The serialised table is locked in
``_authorship_overview_golden.json``. The test compares the live
output to the snapshot field-for-field. If a downstream change moves
a cell intentionally, regenerate the snapshot via the helper at the
bottom of this file.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.enrichment.config import EnrichmentConfig
from src.enrichment.overviews import OVERVIEWS
from src.enrichment.overviews.implementations.authorship_table import (
    COLUMNS,
    AuthorshipTableBuilder,
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
    Path(__file__).parent / "_authorship_overview_golden.json"
)


# ----------------------------------------------------------------------
# Fixture — deterministic ``Graph`` for the golden assertion.
# ----------------------------------------------------------------------
def _build_golden_graph():
    """Deterministic v2 graph: fixed anchor + seeded commits per file.

    Fixture layout:

    * ``src/owner.py`` — Alice 9 × +100 lines, Bob 1 × +10 → Alice
      dominates ~91.4% → fires :pyattr:`anomaly.knowledge.BusFactor1`.
    * ``src/shared.py`` — Alice/Bob alternate 4 commits at +20/-5 each
      → balanced authorship, contributes to the per-folder share.
    * ``tests/test_owner.py`` — Bob only, 2 × +15 → tests/ row keeps a
      single author.

    Anchor is :data:`datetime(2026, 1, 1, tzinfo=UTC)` so trend% / cell
    values stay reproducible across runs.
    """
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    graph, project = build_v2_graph("authorship-golden")
    # The recent-cutoff stub follows the Chunk-11 test-stub convention.
    graph.__dict__["recent_cutoff"] = anchor - timedelta(days=90)

    alice = make_account("Alice", "a@x", project.ref())
    bob = make_account("Bob", "b@x", project.ref())
    graph.git_accounts.add(alice)
    graph.git_accounts.add(bob)

    owner = make_file("src/owner.py", project.ref())
    graph.files.add(owner)
    for i in range(9):
        c = make_commit(
            f"own_{i}", "feat: owner", alice,
            anchor - timedelta(days=10 + i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, owner, added=100)
    c = make_commit(
        "own_b", "feat: owner", bob,
        anchor - timedelta(days=5), project.ref(),
    )
    graph.commits.add(c)
    add_change(graph, c, owner, added=10)

    shared = make_file("src/shared.py", project.ref())
    graph.files.add(shared)
    for i, author in enumerate([alice, bob, alice, bob]):
        c = make_commit(
            f"shr_{i}", "fix: shared", author,
            anchor - timedelta(days=2 + i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, shared, added=20, deleted=5)

    ftest = make_file("tests/test_owner.py", project.ref())
    graph.files.add(ftest)
    for i in range(2):
        c = make_commit(
            f"tst_{i}", "test: harness", bob,
            anchor - timedelta(days=1 + i), project.ref(),
        )
        graph.commits.add(c)
        add_change(graph, c, ftest, added=15, deleted=0)

    run_pipeline(graph, EnrichmentConfig())
    return graph


# ----------------------------------------------------------------------
# Sanity (shape) assertions — kept structural and golden-independent.
# ----------------------------------------------------------------------
def test_authorship_overview_registered():
    assert "authorship" in OVERVIEWS.names()
    assert OVERVIEWS.get("authorship") is AuthorshipTableBuilder


def test_authorship_overview_columns_match_spec():
    graph = _build_golden_graph()
    table = AuthorshipTableBuilder().build(graph, EnrichmentConfig())
    assert table.columns == COLUMNS
    assert table.entity_kind == "component"


def test_authorship_overview_row_ids_match_fixture():
    graph = _build_golden_graph()
    table = AuthorshipTableBuilder().build(graph, EnrichmentConfig())
    row_ids = [r.entity_id for r in table.rows]
    # Synthetic ``(project)`` aggregate + one row per top-level folder
    # that owns at least one file (``src`` + ``tests``).
    assert row_ids == ["(project)", "src", "tests"]


def test_authorship_overview_known_cell_values():
    """Specific cell values from the engineered fixture.

    These are checked outside the JSON snapshot so a downstream rounding
    change surfaces with a clearer assertion than "JSON diff".
    """
    graph = _build_golden_graph()
    table = AuthorshipTableBuilder().build(graph, EnrichmentConfig())
    rows = {r.entity_id: r for r in table.rows}

    project = rows["(project)"]
    assert project.cells["total_authors"].lifetime_value == 2
    # owner.py fires BusFactor1 (Alice ~91% dominance over 2 authors).
    assert project.cells["bus_factor_1_files"].lifetime_value >= 1
    dominant = project.cells["dominant_author_share"].lifetime_value
    assert dominant is not None and 0.0 <= dominant <= 1.0

    tests_row = rows["tests"]
    # tests/ only has Bob → dominant share 1.0, single author.
    assert tests_row.cells["total_authors"].lifetime_value == 1
    assert tests_row.cells["dominant_author_share"].lifetime_value == 1.0


# ----------------------------------------------------------------------
# Locked snapshot — the golden assertion.
# ----------------------------------------------------------------------
def test_authorship_overview_matches_locked_golden():
    """Field-for-field comparison against the locked snapshot.

    The golden was generated against the deterministic fixture above. If
    a downstream change intentionally moves a cell, regenerate the
    snapshot by running the ``__main__`` block at the bottom of this
    file and committing the new ``_authorship_overview_golden.json``.
    """
    if not _GOLDEN_PATH.is_file():
        pytest.skip(
            f"missing golden at {_GOLDEN_PATH}; regenerate via "
            f"`python tests/enrichment/test_authorship_overview_golden.py`"
        )
    expected = json.loads(_GOLDEN_PATH.read_text())

    graph = _build_golden_graph()
    table = AuthorshipTableBuilder().build(graph, EnrichmentConfig())
    actual = table.model_dump(mode="json")

    assert actual == expected


# ----------------------------------------------------------------------
# Regeneration helper — runs the fixture and writes the golden.
# ----------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    graph = _build_golden_graph()
    table = AuthorshipTableBuilder().build(graph, EnrichmentConfig())
    _GOLDEN_PATH.write_text(
        json.dumps(table.model_dump(mode="json"), indent=2, sort_keys=True)
        + "\n"
    )
    print(f"wrote golden to {_GOLDEN_PATH}")
