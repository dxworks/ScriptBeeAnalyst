"""Tests for :class:`PRTraitsMetric` — Chunk 16.

Restored / re-derived from
``git show f840488^:data-server/tests/enrichment/test_a25_pr_review.py``
(StalledReview subset). The legacy file built ``GitHubProject(...)``
with embedded registries; v2 builds the typed entities directly and
registers them on :class:`Graph`.

Covers
------
* StalledReview fires on an open PR with zero reviews older than the
  threshold.
* StalledReview suppressed on a fresh open PR.
* StalledReview suppressed on a merged PR (any age).
* StalledReview suppressed on an old open PR with a *recent* review.
* StalledReview fires on an old open PR with only stale reviews.
* Pipeline registers the metric under the expected name.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.common.domains.github.models import PullRequest, Review
from src.common.kernel import EntityKind, EntityRef, Graph
from src.enrichment.config import EnrichmentConfig
from src.enrichment.metrics import METRICS
from src.enrichment.metrics.implementations.pr_traits import PRTraitsMetric
from src.enrichment.pipeline import run_pipeline


UTC = timezone.utc


@pytest.fixture
def graph_with_anchor() -> Graph:
    """An empty :class:`Graph` with an ``anchor_date`` injected.

    The :pyattr:`Graph.model_config` is ``extra="forbid"`` so we use the
    ``__dict__`` injection pattern documented across Chunk-12 / Chunk-15
    tests for the test-stub anchor convention.
    """
    g = Graph(project_id="pr-traits-test")
    # 2026-05-17 — pinned anchor so the "age" math is deterministic.
    g.__dict__["anchor_date"] = datetime(2026, 5, 17, tzinfo=UTC)
    g.__dict__["project_ref"] = EntityRef(kind=EntityKind.PROJECT, id="proj")
    return g


def _add_pr(
    graph: Graph,
    *,
    number: int,
    state: str,
    created_at: datetime,
    merged_at: datetime | None = None,
    reviews: list[Review] | None = None,
) -> PullRequest:
    project_ref: EntityRef = graph.__dict__["project_ref"]
    pr = PullRequest(
        id=PullRequest.make_id(number),
        project_ref=project_ref,
        number=number,
        title=f"PR {number}",
        state=state,
        created_at=created_at,
        merged_at=merged_at,
        closed_at=None,
        updated_at=created_at,
        review_refs=[r.ref() for r in (reviews or [])],
    )
    graph.pull_requests.add(pr)
    for review in reviews or []:
        graph.reviews.add(review)
    return pr


def _add_review(
    pr_number: int, ordinal: int, state: str, submitted_at: datetime | None
) -> Review:
    return Review(
        id=Review.make_id(pr_number, ordinal),
        pull_request_ref=EntityRef(kind=EntityKind.PULL_REQUEST, id=str(pr_number)),
        ordinal=ordinal,
        state=state,
        body="",
        submitted_at=submitted_at,
    )


def _run(graph: Graph) -> None:
    run_pipeline(graph, EnrichmentConfig())


def _traits_for_pr(graph: Graph, number: int) -> tuple:
    return graph.traits.for_target(
        EntityRef(kind=EntityKind.PULL_REQUEST, id=str(number))
    )


# ----------------------------------------------------------------------
# Registry wiring
# ----------------------------------------------------------------------
def test_pr_traits_metric_is_registered():
    names = {m.name for m in METRICS.all()}
    assert "pr.traits" in names


def test_pr_traits_metric_outputs_metadata():
    cls = next(m for m in METRICS.all() if m.name == "pr.traits")
    assert "anomaly.review.StalledReview" in cls.outputs.emits_traits
    assert "stalled_review_open_days_min" in cls.config_fields


# ----------------------------------------------------------------------
# StalledReview emission
# ----------------------------------------------------------------------
def test_stalled_review_fires_on_old_open_pr_without_reviews(graph_with_anchor):
    anchor: datetime = graph_with_anchor.__dict__["anchor_date"]
    _add_pr(
        graph_with_anchor, number=100, state="open",
        created_at=anchor - timedelta(days=30),
    )
    _run(graph_with_anchor)
    traits = _traits_for_pr(graph_with_anchor, 100)
    names = {t.name for t in traits}
    assert "anomaly.review.StalledReview" in names
    stalled = next(t for t in traits if t.name == "anomaly.review.StalledReview")
    assert stalled.evidence["pr_open_days"] >= 14
    assert stalled.evidence["review_count"] == 0
    assert stalled.evidence["had_reviews"] is False
    assert stalled.severity == float(stalled.evidence["pr_open_days"])


def test_stalled_review_suppressed_on_fresh_open_pr(graph_with_anchor):
    anchor: datetime = graph_with_anchor.__dict__["anchor_date"]
    _add_pr(
        graph_with_anchor, number=101, state="open",
        created_at=anchor - timedelta(days=2),
    )
    _run(graph_with_anchor)
    traits = _traits_for_pr(graph_with_anchor, 101)
    assert "anomaly.review.StalledReview" not in {t.name for t in traits}


def test_stalled_review_suppressed_on_merged_pr(graph_with_anchor):
    anchor: datetime = graph_with_anchor.__dict__["anchor_date"]
    _add_pr(
        graph_with_anchor, number=102, state="merged",
        created_at=anchor - timedelta(days=60),
        merged_at=anchor - timedelta(days=1),
    )
    _run(graph_with_anchor)
    traits = _traits_for_pr(graph_with_anchor, 102)
    assert "anomaly.review.StalledReview" not in {t.name for t in traits}


def test_stalled_review_suppressed_when_last_review_recent(graph_with_anchor):
    """Old open PR whose last review landed inside the threshold — quiet."""
    anchor: datetime = graph_with_anchor.__dict__["anchor_date"]
    review = _add_review(
        103, 0, "COMMENTED", anchor - timedelta(days=2),
    )
    _add_pr(
        graph_with_anchor, number=103, state="open",
        created_at=anchor - timedelta(days=60),
        reviews=[review],
    )
    _run(graph_with_anchor)
    traits = _traits_for_pr(graph_with_anchor, 103)
    assert "anomaly.review.StalledReview" not in {t.name for t in traits}


def test_stalled_review_fires_when_last_review_is_also_stale(graph_with_anchor):
    """Old open PR whose only review is itself older than the threshold."""
    anchor: datetime = graph_with_anchor.__dict__["anchor_date"]
    stale_review = _add_review(
        104, 0, "COMMENTED", anchor - timedelta(days=45),
    )
    _add_pr(
        graph_with_anchor, number=104, state="open",
        created_at=anchor - timedelta(days=60),
        reviews=[stale_review],
    )
    _run(graph_with_anchor)
    traits = _traits_for_pr(graph_with_anchor, 104)
    stalled = next(t for t in traits if t.name == "anomaly.review.StalledReview")
    assert stalled.evidence["review_count"] == 1
    assert stalled.evidence["had_reviews"] is True
    assert stalled.evidence["days_since_last_review"] >= 14
    assert stalled.evidence["review_states_summary"] == {"COMMENTED": 1}


def test_pr_traits_no_op_on_graph_without_prs(graph_with_anchor):
    """Empty PR registry — metric produces nothing, no exceptions."""
    metric = PRTraitsMetric()
    out = list(metric.compute(graph_with_anchor, EnrichmentConfig()))
    assert out == []
