"""Phase 3 relations: coauthor, pr.file, pr.reviewer, component cochange, issue.issue."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.common.models import (
    GitHubProject, GitHubUser, PullRequest, GitHubCommit,
    Issue, IssueStatus, IssueStatusCategory, IssueType, JiraProject, JiraUser,
)
from src.common.github_models import Review
from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import build_synthetic_graph


UTC = timezone.utc


def test_coauthor_emits_pair_when_two_authors_share_files():
    g = build_synthetic_graph()
    e = compute_enrichments(g, EnrichmentConfig())
    rf = e.relation_file("coauthor.author-author", "lifetime")
    assert rf is not None
    # owner.py and buggy.py are both touched by Alice and Bob -> at least one edge.
    assert len(rf.relations) >= 1
    pair = rf.relations[0]
    # symmetric, sorted ids
    assert pair.source_id < pair.target_id
    assert pair.strength >= 1


def test_pr_file_via_linked_commits():
    g = build_synthetic_graph()
    git_proj = g["git"]
    # Pick the first 2 commits that touch buggy.py.
    target_commits = [
        c for c in git_proj.git_commit_registry.all
        if any(getattr(ch, "new_file_name", "").endswith("buggy.py") for ch in c.changes)
    ][:2]
    assert len(target_commits) == 2

    gh = GitHubProject(name="gh")
    pr = PullRequest(
        number=42, title="t", state="merged", changedFiles=1, body="",
        createdAt=datetime.now(UTC), mergedAt=datetime.now(UTC),
        closedAt=datetime.now(UTC), updatedAt=datetime.now(UTC),
        git_commits=target_commits,
    )
    gh.pull_request_registry.add_all([pr])
    g["github"] = gh

    e = compute_enrichments(g, EnrichmentConfig())
    rf = e.relation_file("pr.file", "lifetime")
    assert rf is not None
    edges = [r for r in rf.relations if r.source_id == "42"]
    assert any(r.target_id == "src/buggy.py" for r in edges)
    # Strength is number of commits that touched the file.
    buggy = next(r for r in edges if r.target_id == "src/buggy.py")
    assert buggy.strength == 2


def test_pr_reviewer_proxy_when_no_explicit_reviewers():
    g = build_synthetic_graph()
    gh = GitHubProject(name="gh")
    merger = GitHubUser(url="u1", login="merger", name="Merger")
    assignee = GitHubUser(url="u2", login="aly", name="Aly")
    pr = PullRequest(
        number=7, title="t", state="merged", changedFiles=1, body="",
        createdAt=datetime.now(UTC), mergedAt=datetime.now(UTC),
        closedAt=datetime.now(UTC), updatedAt=datetime.now(UTC),
        mergedBy=merger, assignees=[assignee],
    )
    gh.pull_request_registry.add_all([pr])
    g["github"] = gh

    e = compute_enrichments(g, EnrichmentConfig())
    rf = e.relation_file("pr.reviewer", "lifetime")
    assert rf is not None
    edges = [r for r in rf.relations if r.source_id == "7"]
    assert len(edges) == 2
    # All edges in the fallback path must carry the proxy stamp.
    for r in edges:
        assert r.extras.get("proxy") is True
        assert r.extras.get("source") == "mergedBy+assignees"


def test_pr_reviewer_explicit_reviews_branch():
    """When PullRequest carries `reviews` with APPROVED/CHANGES_REQUESTED states,
    the extractor must use them and NOT fall back to mergedBy+assignees."""
    g = build_synthetic_graph()
    gh = GitHubProject(name="gh")
    rev1 = GitHubUser(url="u1", login="rev1", name="Rev1")
    rev2 = GitHubUser(url="u2", login="rev2", name="Rev2")
    merger = GitHubUser(url="u3", login="merger", name="Merger")
    assignee = GitHubUser(url="u4", login="aly", name="Aly")
    pr = PullRequest(
        number=11, title="t", state="merged", changedFiles=1, body="",
        createdAt=datetime.now(UTC), mergedAt=datetime.now(UTC),
        closedAt=datetime.now(UTC), updatedAt=datetime.now(UTC),
        mergedBy=merger, assignees=[assignee],
        reviews=[
            Review(state="APPROVED", submittedAt=datetime.now(UTC), body="lgtm", user=rev1),
            Review(state="CHANGES_REQUESTED", submittedAt=datetime.now(UTC), body="nit", user=rev2),
        ],
    )
    gh.pull_request_registry.add_all([pr])
    g["github"] = gh

    e = compute_enrichments(g, EnrichmentConfig())
    rf = e.relation_file("pr.reviewer", "lifetime")
    assert rf is not None
    edges = [r for r in rf.relations if r.source_id == "11"]
    # Only the explicit reviewers should be linked -- mergedBy/assignees are
    # ignored when explicit reviews are present.
    target_ids = sorted(r.target_id for r in edges)
    assert target_ids == ["rev1", "rev2"]
    # Explicit branch must stamp proxy=False with source=Review.user.
    for r in edges:
        assert r.extras.get("proxy") is False
        assert r.extras.get("source") == "Review.user"


def test_cochange_component_aggregates_file_pairs():
    g = build_synthetic_graph()
    e = compute_enrichments(g, EnrichmentConfig())
    rf = e.relation_file("cochange.component-component", "lifetime")
    assert rf is not None
    # All files share the same `src` component, so aggregated edges have
    # the same component on both sides — those are dropped (self-loops).
    for r in rf.relations:
        assert r.source_id != r.target_id


def test_cochange_component_cross_component_edge():
    """When mapping splits files into 2 components and they cochange, expect a non-self edge."""
    import json
    import tempfile
    payload = {
        "buggy": {"path_prefix": "src/buggy"},
        "owner": {"path_prefix": "src/owner"},
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(payload, fh)
        path = fh.name

    g = build_synthetic_graph()
    cfg = EnrichmentConfig(components_mapping_path=path)
    e = compute_enrichments(g, cfg)
    rf = e.relation_file("cochange.component-component", "lifetime")
    assert rf is not None
    # The buggy fixture co-changes buggy + owner in 3 commits -> at least one edge.
    pairs = {tuple(sorted((r.source_id, r.target_id))) for r in rf.relations}
    assert ("buggy", "owner") in pairs


def test_issue_issue_native_link_weight():
    """Two issues linked as parent/child: native edge with weight 2."""
    now = datetime.now(UTC)
    proj = JiraProject(name="jp")
    cat = IssueStatusCategory(key="indeterminate", name="In Progress")
    status = IssueStatus(id="1", name="In Progress", issue_status_categories=cat)
    bug = IssueType(id="t1", name="Bug", description="bug", isSubTask=False)

    parent = Issue(
        id=1, key="P-1", summary="parent", createdAt=now, updatedAt=now,
        issue_statuses=[status], issue_types=[bug],
    )
    child = Issue(
        id=2, key="P-2", summary="child", createdAt=now, updatedAt=now,
        issue_statuses=[status], issue_types=[bug],
        parent=parent,
    )
    parent.children = [child]
    proj.issue_registry.add_all([parent, child])
    proj.issue_status_registry.add_all([status])
    proj.issue_status_category_registry.add_all([cat])
    proj.issue_type_registry.add_all([bug])

    g = build_synthetic_graph()
    g["jira"] = proj
    e = compute_enrichments(g, EnrichmentConfig())
    rf = e.relation_file("issue.issue", "lifetime")
    assert rf is not None
    edges = [r for r in rf.relations if {r.source_id, r.target_id} == {"P-1", "P-2"}]
    assert len(edges) == 1
    edge = edges[0]
    assert edge.extras.get("native_link") is True
    assert edge.strength >= 2.0
