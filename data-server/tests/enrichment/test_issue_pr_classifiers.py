"""Issue/PR classifiers must populate the expected slots from the fixture."""
from __future__ import annotations

from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import (
    build_github_fixture,
    build_jira_fixture,
    build_synthetic_graph,
)


def _enrich():
    g = build_synthetic_graph()
    g["jira"] = build_jira_fixture()
    g["github"] = build_github_fixture()
    return compute_enrichments(g, EnrichmentConfig())


def test_issue_classifiers_populate_status_type_resolution():
    e = _enrich()
    proj1 = e.tags_by_entity.get("issue:PROJ-1")
    assert proj1 is not None
    assert proj1.classifiers["status"] == "In Progress"
    assert proj1.classifiers["type"] == "Bug"
    assert proj1.classifiers["resolution"] == "open"
    assert proj1.classifiers["age_bucket"] == ">1y"

    proj2 = e.tags_by_entity.get("issue:PROJ-2")
    assert proj2 is not None
    assert proj2.classifiers["resolution"] == "resolved"


def test_pr_classifiers_state_and_size():
    e = _enrich()
    pr_small = e.tags_by_entity.get("pr:7")
    pr_big = e.tags_by_entity.get("pr:8")
    assert pr_small is not None
    assert pr_small.classifiers["state"] == "merged"
    assert pr_small.classifiers["size"] == "XS"
    assert pr_big is not None
    assert pr_big.classifiers["state"] == "open"
    # 300 changedFiles > pr_size_s_max=200 but <= pr_size_m_max=600
    assert pr_big.classifiers["size"] == "M"
