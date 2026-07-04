"""Tests for the :class:`CommitTaskPrefixClassifierMetric` (Phase 2 D3)."""
from __future__ import annotations

from datetime import datetime

from src.enrichment.metrics import METRICS
from src.enrichment.metrics.implementations.commit_task_prefixes import (
    CommitTaskPrefixClassifierMetric,
)
from src.enrichment.tags import Classifier
from src.enrichment.utils.task_prefix import (
    extract_task_prefixes,
    parse_task_prefix,
)

from tests.enrichment.conftest import (
    UTC,
    build_v2_graph,
    make_account,
    make_commit,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _seed_commit(graph, project, sha: str, message: str):
    alice = next(iter(graph.git_accounts), None)
    if alice is None:
        alice = make_account("Alice", "a@x", project.ref())
        graph.git_accounts.add(alice)
    c = make_commit(sha, message, alice, datetime(2024, 6, 1, tzinfo=UTC), project.ref())
    graph.commits.add(c)
    return c


# ----------------------------------------------------------------------
# task_prefix helpers (locked regex contract)
# ----------------------------------------------------------------------
def test_parse_task_prefix_matches_jira_style_at_start() -> None:
    assert parse_task_prefix("PROJ-12: fix bug") == ("PROJ", ": fix bug")
    assert parse_task_prefix("AB1-7 hello") == ("AB1", " hello")
    assert parse_task_prefix("  PROJ-3 leading space") == ("PROJ", " leading space")


def test_parse_task_prefix_rejects_mid_message_keys() -> None:
    # The "first key at the start" contract excludes mid-message refs.
    assert parse_task_prefix("merge PROJ-12 in") is None
    assert parse_task_prefix("") is None
    assert parse_task_prefix("no key") is None
    assert parse_task_prefix("a-12 too-short prefix") is None


def test_extract_task_prefixes_dedupes_and_preserves_order() -> None:
    msg = "PROJ-1: also see OTHER-9, PROJ-2 and BACK-3"
    assert extract_task_prefixes(msg) == ["PROJ", "OTHER", "BACK"]


def test_extract_task_prefixes_empty_message_is_empty_list() -> None:
    assert extract_task_prefixes("") == []
    assert extract_task_prefixes("plain message, no key") == []


# ----------------------------------------------------------------------
# Metric registration
# ----------------------------------------------------------------------
def test_metric_is_registered_under_expected_name() -> None:
    assert "commit_task_prefixes" in METRICS
    assert METRICS.get("commit_task_prefixes") is CommitTaskPrefixClassifierMetric


# ----------------------------------------------------------------------
# Metric.compute behaviour
# ----------------------------------------------------------------------
def test_commit_with_task_prefix_emits_classifier() -> None:
    graph, project = build_v2_graph()
    _seed_commit(graph, project, "abc123", "PROJ-42: fix the regression")

    out = list(CommitTaskPrefixClassifierMetric().compute(graph, config=None))
    assert len(out) == 1
    c = out[0]
    assert isinstance(c, Classifier)
    assert c.dimension == "task_prefix"
    assert c.value == "PROJ"
    assert c.target.id == "abc123"


def test_commit_with_multiple_keys_emits_one_classifier_per_prefix() -> None:
    graph, project = build_v2_graph()
    _seed_commit(graph, project, "deadbe", "PROJ-1 and OTHER-9 together")

    out = list(CommitTaskPrefixClassifierMetric().compute(graph, config=None))
    values = sorted(c.value for c in out)
    assert values == ["OTHER", "PROJ"]
    # Every classifier targets the same commit.
    assert {c.target.id for c in out} == {"deadbe"}


def test_commit_without_task_prefix_emits_no_classifier() -> None:
    graph, project = build_v2_graph()
    _seed_commit(graph, project, "c1", "chore: bump deps")
    out = list(CommitTaskPrefixClassifierMetric().compute(graph, config=None))
    assert out == []
