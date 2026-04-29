"""Relations: ownership and issue.file."""
from __future__ import annotations

from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import (
    build_github_fixture,
    build_jira_fixture,
    build_synthetic_graph,
)


def test_ownership_relations_have_per_file_strengths_summing_to_one():
    g = build_synthetic_graph()
    e = compute_enrichments(g, EnrichmentConfig())
    rf = e.relation_file("ownership.author-file", "lifetime")
    assert rf is not None
    # Group strengths by file -> should sum to ~1.0 per file (relative shares).
    by_file: dict[str, float] = {}
    for r in rf.relations:
        by_file[r.target_id] = by_file.get(r.target_id, 0.0) + r.strength
    for fid, total in by_file.items():
        assert 0.99 <= total <= 1.01, f"{fid}: {total}"


def test_issue_file_relations_emit_when_jira_present():
    g = build_synthetic_graph()
    g["jira"] = build_jira_fixture()
    g["github"] = build_github_fixture()
    e = compute_enrichments(g, EnrichmentConfig())
    rf = e.relation_file("issue.file", "lifetime")
    assert rf is not None
    # No commits are linked in the fixture, so the count is 0 — but the
    # relation file itself must be present so the endpoint won't 404.
    assert isinstance(rf.relations, list)
