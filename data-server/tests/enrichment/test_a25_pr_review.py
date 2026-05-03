"""A2.5 PR-side bonus: review_intensity, StalledReview, pr_lifecycle table,
and the true `pr.reviewer` relation backed by `Review.user`."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.common.github_models import Review
from src.common.models import GitHubProject, GitHubUser, PullRequest
from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import build_synthetic_graph


UTC = timezone.utc


def _make_pr(number: int, *, reviews: list[Review] | None = None,
             state: str = "open", created_at: datetime | None = None,
             merged_at: datetime | None = None) -> PullRequest:
    base = created_at or datetime.now(UTC)
    return PullRequest(
        number=number,
        title=f"PR{number}",
        state=state,
        changedFiles=1,
        body="",
        createdAt=base,
        mergedAt=merged_at,
        closedAt=None,
        updatedAt=base,
        reviews=reviews or [],
    )


def _enrich_with_prs(prs: list[PullRequest]) -> object:
    g = build_synthetic_graph()
    gh = GitHubProject(name="gh")
    gh.pull_request_registry.add_all(prs)
    g["github"] = gh
    return compute_enrichments(g, EnrichmentConfig())


def test_review_intensity_bucket_thresholds():
    """0 -> none; 2 -> light; 3 -> moderate; 5 -> heavy."""
    now = datetime.now(UTC)

    def _reviews(n: int) -> list[Review]:
        return [
            Review(state="COMMENTED", submittedAt=now, body=f"r{i}", user=None)
            for i in range(n)
        ]

    pr_none = _make_pr(1, reviews=[])
    pr_light = _make_pr(2, reviews=_reviews(2))
    pr_moderate = _make_pr(3, reviews=_reviews(3))
    pr_heavy = _make_pr(4, reviews=_reviews(5))

    e = _enrich_with_prs([pr_none, pr_light, pr_moderate, pr_heavy])

    assert e.tags_by_entity["pr:1"].classifiers["review_intensity"] == "none"
    assert e.tags_by_entity["pr:2"].classifiers["review_intensity"] == "light"
    assert e.tags_by_entity["pr:3"].classifiers["review_intensity"] == "moderate"
    assert e.tags_by_entity["pr:4"].classifiers["review_intensity"] == "heavy"


def test_stalled_review_fires_on_old_open_pr_without_reviews():
    """Open PR older than threshold with no reviews fires StalledReview;
    a merged PR (any age) never does."""
    now = datetime.now(UTC)
    stalled = _make_pr(
        100, reviews=[], state="open",
        created_at=now - timedelta(days=30),
    )
    fresh_open = _make_pr(
        101, reviews=[], state="open",
        created_at=now - timedelta(days=2),
    )
    merged_old = _make_pr(
        102, reviews=[], state="merged",
        created_at=now - timedelta(days=60),
        merged_at=now - timedelta(days=1),
    )

    e = _enrich_with_prs([stalled, fresh_open, merged_old])

    stalled_traits = [t.name for t in e.tags_by_entity["pr:100"].traits]
    assert "anomaly.review.StalledReview" in stalled_traits
    evidence = next(
        t for t in e.tags_by_entity["pr:100"].traits
        if t.name == "anomaly.review.StalledReview"
    ).evidence
    assert evidence["pr_open_days"] >= 14
    assert evidence["days_since_last_review"] is None
    assert evidence["review_count"] == 0

    fresh_traits = [t.name for t in e.tags_by_entity["pr:101"].traits]
    assert "anomaly.review.StalledReview" not in fresh_traits

    merged_tags = e.tags_by_entity.get("pr:102")
    if merged_tags is not None:
        assert "anomaly.review.StalledReview" not in [
            t.name for t in merged_tags.traits
        ]


def test_pr_lifecycle_table_has_review_turnaround_for_merged_pr():
    """One merged PR with a review submitted 6h before merge -> 6.0 hours."""
    now = datetime.now(UTC)
    review_at = now - timedelta(hours=6)
    merged_at = now
    pr = _make_pr(
        200,
        reviews=[Review(state="APPROVED", submittedAt=review_at, body="ok", user=None)],
        state="merged",
        created_at=now - timedelta(days=2),
        merged_at=merged_at,
    )

    e = _enrich_with_prs([pr])

    table = e.overview("pr_lifecycle")
    assert table is not None
    assert table.entity_kind == "project"
    assert table.columns == ["review_turnaround_hours"]
    assert len(table.rows) == 1
    project_row = table.rows[0]
    assert project_row.entity_id == "(project)"
    cell = project_row.cells["review_turnaround_hours"]
    assert cell.lifetime_value is not None
    # 6 hours within rounding tolerance
    assert abs(cell.lifetime_value - 6.0) < 0.05


def test_pr_reviewer_uses_review_user_when_available():
    """PR with one APPROVED review -> proxy=False edge from Review.user."""
    now = datetime.now(UTC)
    reviewer = GitHubUser(url="u-r", login="reviewer", name="Reviewer")
    merger = GitHubUser(url="u-m", login="merger", name="Merger")
    pr = PullRequest(
        number=300, title="t", state="merged", changedFiles=1, body="",
        createdAt=now, mergedAt=now, closedAt=now, updatedAt=now,
        mergedBy=merger,
        reviews=[Review(state="APPROVED", submittedAt=now, body="lgtm", user=reviewer)],
    )

    g = build_synthetic_graph()
    gh = GitHubProject(name="gh")
    gh.pull_request_registry.add_all([pr])
    g["github"] = gh
    e = compute_enrichments(g, EnrichmentConfig())

    rf = e.relation_file("pr.reviewer", "lifetime")
    assert rf is not None
    edges = [r for r in rf.relations if r.source_id == "300"]
    assert len(edges) == 1
    edge = edges[0]
    assert edge.target_id == "reviewer"
    assert edge.extras.get("proxy") is False
    assert edge.extras.get("source") == "Review.user"


def test_pr_reviewer_falls_back_to_proxy_when_no_qualifying_reviews():
    """PR with 0 reviews -> mergedBy+assignees fallback with proxy=True."""
    now = datetime.now(UTC)
    merger = GitHubUser(url="u-m", login="merger", name="Merger")
    assignee = GitHubUser(url="u-a", login="aly", name="Aly")
    pr = PullRequest(
        number=301, title="t", state="merged", changedFiles=1, body="",
        createdAt=now, mergedAt=now, closedAt=now, updatedAt=now,
        mergedBy=merger, assignees=[assignee],
        reviews=[],
    )

    g = build_synthetic_graph()
    gh = GitHubProject(name="gh")
    gh.pull_request_registry.add_all([pr])
    g["github"] = gh
    e = compute_enrichments(g, EnrichmentConfig())

    rf = e.relation_file("pr.reviewer", "lifetime")
    assert rf is not None
    edges = [r for r in rf.relations if r.source_id == "301"]
    assert len(edges) == 2
    for r in edges:
        assert r.extras.get("proxy") is True
        assert r.extras.get("source") == "mergedBy+assignees"
