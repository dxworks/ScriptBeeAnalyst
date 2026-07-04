"""Tests for :class:`IssuePRClassifierMetric` — Chunk 16.

Re-derived from the legacy
``test_issue_pr_classifiers.py`` / ``test_pr_size_buckets.py`` tests
(both deleted by Chunk 10). The legacy used the kitchen-sink
``build_synthetic_graph`` + ``build_jira_fixture`` /
``build_github_fixture`` fixtures (now gone). This v2 port builds tiny
per-test graphs with just enough typed entities to exercise one
classifier slot at a time.

Covers
------
* Issue classifiers — ``issue.status`` / ``issue.type`` /
  ``issue.resolution`` / ``issue.age`` slots; status-category vs
  status-name resolution; age math uses ``resolution_date`` /
  ``updated_at`` for closed issues, anchor for open ones; age-bucket
  labels round through the default-config thresholds.
* PR classifiers — ``pr.state`` (native), ``pr.size`` (every bucket
  boundary from XS → XL), ``pr.review_intensity`` (none / light /
  moderate / heavy buckets).
* Registry wiring — the metric is registered and exposes the seven
  expected classifier dimensions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.common.domains.github.models import PullRequest, Review
from src.common.domains.jira.models import Issue, IssueStatus, IssueType
from src.common.kernel import EntityKind, EntityRef, Graph
from src.enrichment.config import EnrichmentConfig
from src.enrichment.metrics import METRICS
from src.enrichment.metrics.implementations.issue_pr_classifiers import (
    IssuePRClassifierMetric,
)
from src.enrichment.pipeline import run_pipeline


UTC = timezone.utc


# ----------------------------------------------------------------------
# Fixtures + helpers
# ----------------------------------------------------------------------
@pytest.fixture
def graph_with_anchor() -> Graph:
    g = Graph(project_id="issue-pr-test")
    g.__dict__["anchor_date"] = datetime(2026, 5, 17, tzinfo=UTC)
    g.__dict__["project_ref"] = EntityRef(kind=EntityKind.PROJECT, id="proj")
    return g


def _add_issue_status(graph: Graph, status_id: str, name: str, category: str) -> IssueStatus:
    project_ref: EntityRef = graph.__dict__["project_ref"]
    status = IssueStatus(
        id=status_id, project_ref=project_ref, name=name, category=category,
    )
    graph.issue_statuses.add(status)
    return status


def _add_issue_type(graph: Graph, type_id: str, name: str) -> IssueType:
    project_ref: EntityRef = graph.__dict__["project_ref"]
    type_ = IssueType(id=type_id, project_ref=project_ref, name=name)
    graph.issue_types.add(type_)
    return type_


def _add_issue(
    graph: Graph,
    *,
    key: str,
    created_at: datetime,
    updated_at: datetime,
    status: IssueStatus,
    type_: IssueType,
    resolution_date: datetime | None = None,
) -> Issue:
    project_ref: EntityRef = graph.__dict__["project_ref"]
    issue = Issue(
        id=key,
        project_ref=project_ref,
        key=key,
        summary=f"summary {key}",
        created_at=created_at,
        updated_at=updated_at,
        status_ref=status.ref(),
        type_ref=type_.ref(),
        resolution_date=resolution_date,
    )
    graph.issues.add(issue)
    return issue


def _add_pr(
    graph: Graph,
    *,
    number: int,
    state: str,
    changed_files: int = 0,
    reviews: list[Review] | None = None,
    created_at: datetime | None = None,
) -> PullRequest:
    project_ref: EntityRef = graph.__dict__["project_ref"]
    base = created_at or graph.__dict__["anchor_date"]
    pr = PullRequest(
        id=PullRequest.make_id(number),
        project_ref=project_ref,
        number=number,
        title=f"PR{number}",
        state=state,
        changed_files=changed_files,
        created_at=base,
        updated_at=base,
        review_refs=[r.ref() for r in (reviews or [])],
    )
    graph.pull_requests.add(pr)
    for r in reviews or []:
        graph.reviews.add(r)
    return pr


def _add_review(
    pr_number: int, ordinal: int, state: str, submitted_at: datetime | None = None,
) -> Review:
    return Review(
        id=Review.make_id(pr_number, ordinal),
        pull_request_ref=EntityRef(kind=EntityKind.PULL_REQUEST, id=str(pr_number)),
        ordinal=ordinal,
        state=state,
        body="",
        submitted_at=submitted_at,
    )


def _classifier_for(graph: Graph, target: EntityRef, dimension: str) -> str | None:
    by_dim = graph.classifiers.for_target(target)
    cls_obj = by_dim.get(dimension)
    return cls_obj.value if cls_obj is not None else None


def _run(graph: Graph) -> None:
    run_pipeline(graph, EnrichmentConfig())


# ----------------------------------------------------------------------
# Registry wiring
# ----------------------------------------------------------------------
def test_issue_pr_classifier_metric_is_registered():
    assert any(m.name == "issue_pr.classifiers" for m in METRICS.all())


def test_emits_seven_expected_dimensions():
    cls = next(m for m in METRICS.all() if m.name == "issue_pr.classifiers")
    expected = {
        "issue.status", "issue.type", "issue.resolution", "issue.age",
        "pr.state", "pr.size", "pr.review_intensity",
    }
    assert expected == set(cls.outputs.emits_classifiers)


# ----------------------------------------------------------------------
# Issue side — status / type / resolution / age
# ----------------------------------------------------------------------
def test_issue_classifiers_populate_native_status_and_type(graph_with_anchor):
    anchor: datetime = graph_with_anchor.__dict__["anchor_date"]
    status = _add_issue_status(
        graph_with_anchor, "S1", "In Progress", "indeterminate",
    )
    type_ = _add_issue_type(graph_with_anchor, "T1", "Bug")
    issue = _add_issue(
        graph_with_anchor, key="PROJ-1",
        created_at=anchor - timedelta(days=400),
        updated_at=anchor - timedelta(days=10),
        status=status, type_=type_,
    )
    _run(graph_with_anchor)
    ref = issue.ref()
    assert _classifier_for(graph_with_anchor, ref, "issue.status") == "In Progress"
    assert _classifier_for(graph_with_anchor, ref, "issue.type") == "Bug"
    assert _classifier_for(graph_with_anchor, ref, "issue.resolution") == "open"
    assert _classifier_for(graph_with_anchor, ref, "issue.age") == ">1y"


def test_issue_resolution_resolved_when_status_category_done(graph_with_anchor):
    anchor: datetime = graph_with_anchor.__dict__["anchor_date"]
    status = _add_issue_status(graph_with_anchor, "S2", "Done", "done")
    type_ = _add_issue_type(graph_with_anchor, "T2", "Story")
    issue = _add_issue(
        graph_with_anchor, key="PROJ-2",
        created_at=anchor - timedelta(days=10),
        updated_at=anchor - timedelta(days=5),
        status=status, type_=type_,
    )
    _run(graph_with_anchor)
    assert _classifier_for(graph_with_anchor, issue.ref(), "issue.resolution") == "resolved"


def test_issue_resolution_resolved_when_status_name_in_resolved_set(
    graph_with_anchor,
):
    """No category set, status name itself is in the resolved vocabulary."""
    anchor: datetime = graph_with_anchor.__dict__["anchor_date"]
    status = _add_issue_status(graph_with_anchor, "S3", "Closed", "")
    type_ = _add_issue_type(graph_with_anchor, "T3", "Task")
    issue = _add_issue(
        graph_with_anchor, key="PROJ-3",
        created_at=anchor - timedelta(days=10),
        updated_at=anchor - timedelta(days=5),
        status=status, type_=type_,
    )
    _run(graph_with_anchor)
    assert _classifier_for(graph_with_anchor, issue.ref(), "issue.resolution") == "resolved"


def test_issue_age_anchored_at_updated_for_closed(graph_with_anchor):
    """Closed issue's age uses ``updated_at`` (or ``resolution_date``), not anchor."""
    anchor: datetime = graph_with_anchor.__dict__["anchor_date"]
    status = _add_issue_status(graph_with_anchor, "S4", "Done", "done")
    type_ = _add_issue_type(graph_with_anchor, "T4", "Task")
    # Created 400 days before anchor, updated only 5 days after creation:
    # age should be 5 days (the resolved span), not 400.
    created = anchor - timedelta(days=400)
    updated = created + timedelta(days=5)
    issue = _add_issue(
        graph_with_anchor, key="PROJ-4",
        created_at=created, updated_at=updated,
        status=status, type_=type_,
    )
    _run(graph_with_anchor)
    # 5 days falls in <1w bucket.
    assert _classifier_for(graph_with_anchor, issue.ref(), "issue.age") == "<1w"


def test_issue_age_uses_resolution_date_when_present(graph_with_anchor):
    """``resolution_date`` overrides ``updated_at`` for closed issues."""
    anchor: datetime = graph_with_anchor.__dict__["anchor_date"]
    status = _add_issue_status(graph_with_anchor, "S5", "Done", "done")
    type_ = _add_issue_type(graph_with_anchor, "T5", "Task")
    created = anchor - timedelta(days=400)
    # updated_at is recent (would yield ~400d) but resolution_date is the
    # canonical end-of-life.
    issue = _add_issue(
        graph_with_anchor, key="PROJ-5",
        created_at=created,
        updated_at=anchor - timedelta(days=1),
        status=status, type_=type_,
        resolution_date=created + timedelta(days=20),
    )
    _run(graph_with_anchor)
    # 20 days → "1-4w" bucket.
    assert _classifier_for(graph_with_anchor, issue.ref(), "issue.age") == "1-4w"


# ----------------------------------------------------------------------
# PR side — state / size buckets
# ----------------------------------------------------------------------
def _pr_size_bucket(graph: Graph, number: int, changed_files: int) -> str:
    _add_pr(graph, number=number, state="open", changed_files=changed_files)
    _run(graph)
    return _classifier_for(
        graph, EntityRef(kind=EntityKind.PULL_REQUEST, id=str(number)),
        "pr.size",
    )


def test_pr_size_buckets_cover_xs_through_xl(graph_with_anchor):
    cfg = EnrichmentConfig()
    # XS bucket at 0 changed files.
    assert _pr_size_bucket(graph_with_anchor, 200, 0) == "XS"
    assert _pr_size_bucket(graph_with_anchor, 201, cfg.pr_size_xs_max + 1) == "S"
    assert _pr_size_bucket(graph_with_anchor, 202, cfg.pr_size_s_max + 1) == "M"
    assert _pr_size_bucket(graph_with_anchor, 203, cfg.pr_size_m_max + 1) == "L"
    assert _pr_size_bucket(graph_with_anchor, 204, cfg.pr_size_l_max + 1) == "XL"


def test_pr_state_is_passthrough_of_native_value(graph_with_anchor):
    _add_pr(graph_with_anchor, number=210, state="merged")
    _run(graph_with_anchor)
    assert _classifier_for(
        graph_with_anchor,
        EntityRef(kind=EntityKind.PULL_REQUEST, id="210"),
        "pr.state",
    ) == "merged"


# ----------------------------------------------------------------------
# PR side — review intensity
# ----------------------------------------------------------------------
def test_review_intensity_buckets_cover_all_four_labels(graph_with_anchor):
    """0 → none ; 1 → light ; 3 → moderate ; 5 → heavy."""
    anchor: datetime = graph_with_anchor.__dict__["anchor_date"]
    # PR300: 0 reviews → none
    _add_pr(graph_with_anchor, number=300, state="open", reviews=[])
    # PR301: 1 COMMENTED → light (light_max default = 2)
    _add_pr(
        graph_with_anchor, number=301, state="open",
        reviews=[_add_review(301, 0, "COMMENTED", anchor)],
    )
    # PR302: 3 reviews → moderate (light_max=2, heavy_min=5 → 3 sits between)
    _add_pr(
        graph_with_anchor, number=302, state="open",
        reviews=[_add_review(302, i, "COMMENTED", anchor) for i in range(3)],
    )
    # PR303: 5 reviews → heavy (heavy_min=5)
    _add_pr(
        graph_with_anchor, number=303, state="open",
        reviews=[_add_review(303, i, "COMMENTED", anchor) for i in range(5)],
    )
    _run(graph_with_anchor)

    def _intensity(n: int) -> str:
        return _classifier_for(
            graph_with_anchor,
            EntityRef(kind=EntityKind.PULL_REQUEST, id=str(n)),
            "pr.review_intensity",
        )

    assert _intensity(300) == "none"
    assert _intensity(301) == "light"
    assert _intensity(302) == "moderate"
    assert _intensity(303) == "heavy"


def test_review_intensity_excludes_dismissed_states(graph_with_anchor):
    """DISMISSED reviews don't count toward intensity."""
    anchor: datetime = graph_with_anchor.__dict__["anchor_date"]
    _add_pr(
        graph_with_anchor, number=310, state="open",
        reviews=[_add_review(310, i, "DISMISSED", anchor) for i in range(10)],
    )
    _run(graph_with_anchor)
    assert _classifier_for(
        graph_with_anchor,
        EntityRef(kind=EntityKind.PULL_REQUEST, id="310"),
        "pr.review_intensity",
    ) == "none"


# ----------------------------------------------------------------------
# No-op safety
# ----------------------------------------------------------------------
def test_metric_no_op_on_empty_graph(graph_with_anchor):
    metric = IssuePRClassifierMetric()
    out = list(metric.compute(graph_with_anchor, EnrichmentConfig()))
    assert out == []
