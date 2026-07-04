"""Tests for :class:`NatureTableBuilder` (Chunk 12).

The legacy ``test_authorship_overview_golden.py`` did not cover the
nature table specifically; this v2-native suite is the regression
checklist for Chunk 12's overview port.

Builder behaviour:

* One synthetic ``(project)`` row + one row per top-level folder a
  commit touched (commits multi-belong to every folder of their
  changes).
* 8 columns (``<nature>_pct``), one per natural-language commit kind.
* Cells are ``%-share lifetime + recent + trend%`` triples; recent
  collapses to lifetime when no ``recent_cutoff`` is attached to the
  graph.
* Nature read from ``graph.classifiers`` on dimension ``message.nature``
  (emitted by :class:`CommitClassifierMetric` in the same pipeline).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.enrichment.config import EnrichmentConfig
from src.enrichment.metrics.implementations.commit_classifiers import (
    CommitClassifierMetric,
)
from src.enrichment.overviews import OVERVIEWS
from src.enrichment.overviews.implementations.nature_table import (
    NatureTableBuilder,
)

from tests.enrichment.conftest import (
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)


UTC = timezone.utc


# ----------------------------------------------------------------------
# Catalog wiring
# ----------------------------------------------------------------------
def test_builder_is_registered():
    assert "nature" in OVERVIEWS.names()
    assert OVERVIEWS.get("nature") is NatureTableBuilder


def test_table_columns_match_spec():
    graph, _ = build_v2_graph("nat-cols")
    tbl = NatureTableBuilder().build(graph, EnrichmentConfig())
    assert tbl.columns == [
        "bugfix_pct", "feature_pct", "refactor_pct", "docs_pct",
        "test_pct", "chore_pct", "merge_pct", "revert_pct",
    ]
    assert tbl.entity_kind == "component"


# ----------------------------------------------------------------------
# Empty graph behaviour
# ----------------------------------------------------------------------
def test_empty_graph_has_only_project_row():
    graph, _ = build_v2_graph("nat-empty")
    tbl = NatureTableBuilder().build(graph, EnrichmentConfig())
    assert [r.entity_id for r in tbl.rows] == ["(project)"]
    # Every cell on the (project) row is None / None because there are
    # no commits → share() returns None.
    proj_row = tbl.rows[0]
    for col in tbl.columns:
        assert proj_row.cells[col].lifetime_value is None
        assert proj_row.cells[col].recent_value is None


# ----------------------------------------------------------------------
# Folder bucketing + nature mix
# ----------------------------------------------------------------------
def _seed(graph, project, *, when, message, file_path, sha):
    """Convenience to add one commit + one change to ``file_path``."""
    author = None
    for a in graph.git_accounts:
        author = a
        break
    if author is None:
        author = make_account("Author", "author@example.com", project.ref())
        graph.git_accounts.add(author)
    f = next((fl for fl in graph.files if fl.id == file_path), None)
    if f is None:
        f = make_file(file_path, project.ref())
        graph.files.add(f)
    c = make_commit(sha, message, author, when, project.ref())
    graph.commits.add(c)
    add_change(graph, c, f)
    return c


def _classify(graph) -> None:
    """Run only ``CommitClassifierMetric`` so ``message.nature`` rows exist."""
    for emitted in CommitClassifierMetric().compute(graph, EnrichmentConfig()):
        # CommitClassifierMetric emits only Classifier rows.
        graph.classifiers.add(emitted)


def test_folder_split_and_nature_shares():
    """Commits in different top-folders show up under separate rows."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("nat-mix")
    _seed(graph, project, when=now - timedelta(days=5),
          message="fix: critical bug", file_path="src/a.py", sha="c1")
    _seed(graph, project, when=now - timedelta(days=4),
          message="fix: another", file_path="src/b.py", sha="c2")
    _seed(graph, project, when=now - timedelta(days=3),
          message="add tests for x", file_path="tests/c.py", sha="c3")

    _classify(graph)
    tbl = NatureTableBuilder().build(graph, EnrichmentConfig())

    rows = {r.entity_id: r for r in tbl.rows}
    assert set(rows) == {"(project)", "src", "tests"}

    # (project) row: 2 bugfix / 3 commits = 66.67%, 1 test / 3 = 33.33%.
    proj = rows["(project)"]
    assert proj.cells["bugfix_pct"].lifetime_value == 66.67
    assert proj.cells["test_pct"].lifetime_value == 33.33
    assert proj.cells["feature_pct"].lifetime_value == 0.0

    # src row: 2 bugfix / 2 commits = 100%.
    src_row = rows["src"]
    assert src_row.cells["bugfix_pct"].lifetime_value == 100.0
    assert src_row.cells["test_pct"].lifetime_value == 0.0

    # tests row: 1 test / 1 commit = 100%.
    tests_row = rows["tests"]
    assert tests_row.cells["test_pct"].lifetime_value == 100.0
    assert tests_row.cells["bugfix_pct"].lifetime_value == 0.0


def test_commit_multi_belongs_to_every_touched_folder():
    """A commit that touches both src/ AND tests/ counts in BOTH rows."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("nat-multi")
    author = make_account("A", "a@x", project.ref())
    graph.git_accounts.add(author)
    f_src = make_file("src/x.py", project.ref())
    f_tst = make_file("tests/x.py", project.ref())
    graph.files.add(f_src)
    graph.files.add(f_tst)
    c = make_commit(
        "c1", "feat: cross-folder", author, now - timedelta(days=2), project.ref(),
    )
    graph.commits.add(c)
    add_change(graph, c, f_src)
    add_change(graph, c, f_tst)

    _classify(graph)
    tbl = NatureTableBuilder().build(graph, EnrichmentConfig())
    rows = {r.entity_id: r for r in tbl.rows}
    # Both folder rows include the commit → both show 100% feature.
    assert rows["src"].cells["feature_pct"].lifetime_value == 100.0
    assert rows["tests"].cells["feature_pct"].lifetime_value == 100.0


def test_recent_cutoff_partitions_share():
    """With ``graph.recent_cutoff`` attached, recent_value reflects only
    in-window commits."""
    now = datetime.now(UTC)
    graph, project = build_v2_graph("nat-cutoff")
    _seed(graph, project, when=now - timedelta(days=200),
          message="fix: old bug", file_path="src/a.py", sha="old")
    _seed(graph, project, when=now - timedelta(days=2),
          message="add: new feat", file_path="src/a.py", sha="new")
    _classify(graph)

    # Attach the cutoff on the graph (bypasses Pydantic extra=forbid via
    # the instance __dict__ — matches the legacy stub-host pattern).
    graph.__dict__["recent_cutoff"] = now - timedelta(days=90)
    tbl = NatureTableBuilder().build(graph, EnrichmentConfig())
    src_row = next(r for r in tbl.rows if r.entity_id == "src")
    # Lifetime: 50% bugfix, 50% feature. Recent: 0% bugfix, 100% feature.
    assert src_row.cells["bugfix_pct"].lifetime_value == 50.0
    assert src_row.cells["bugfix_pct"].recent_value == 0.0
    assert src_row.cells["feature_pct"].recent_value == 100.0
