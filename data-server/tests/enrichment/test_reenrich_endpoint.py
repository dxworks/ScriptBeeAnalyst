"""POST /projects/{id}/reenrich endpoint behaviour.

We don't hit Supabase: the repository.save call is wrapped in a try/except that
demotes failures to "persisted=false", and the pipeline reads the in-memory
graph only. We seed graph_data + current_project_id to bypass the load step.
"""
from __future__ import annotations

import os
import pytest

# Ensure the server module imports cleanly without a real Supabase config.
os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake")
os.environ.setdefault("WORKSPACE_ROOT", "/tmp")

from fastapi.testclient import TestClient  # noqa: E402

from src import server  # noqa: E402
from tests.enrichment.fixtures import build_synthetic_graph  # noqa: E402


PROJECT_ID = "test-project-1"


@pytest.fixture
def client():
    seeded = build_synthetic_graph()
    server.graph_data.clear()
    server.graph_data.update(seeded)
    server.current_project_id = PROJECT_ID
    try:
        yield TestClient(server.app)
    finally:
        server.graph_data.clear()
        server.current_project_id = None


def test_reenrich_happy_path_with_scalar_override(client):
    response = client.post(
        f"/projects/{PROJECT_ID}/reenrich",
        json={"overrides": {"bugmagnet_ratio_min": 0.99}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == PROJECT_ID
    assert body["applied_overrides"] == {"bugmagnet_ratio_min": 0.99}
    summary = body["summary"]
    assert summary["entity_tags_count"] >= 1
    # Raising the threshold to 0.99 should kill BugMagnet.
    e = server.graph_data["enrichments"]
    bm = [
        t for t in e.tags_by_entity.values()
        if any(tr.name == "anomaly.testing.BugMagnet" for tr in t.traits)
    ]
    assert bm == []


def test_reenrich_rejects_pattern_field_override(client):
    response = client.post(
        f"/projects/{PROJECT_ID}/reenrich",
        json={"overrides": {"nature_patterns": [["evil", ".*"]]}},
    )
    assert response.status_code == 400
    assert "nature_patterns" in response.json()["error"]


def test_reenrich_rejects_unknown_key(client):
    response = client.post(
        f"/projects/{PROJECT_ID}/reenrich",
        json={"overrides": {"definitely_not_a_field": 1}},
    )
    assert response.status_code == 400
    assert "definitely_not_a_field" in response.json()["error"]


def test_reenrich_400_when_project_not_loaded(client):
    response = client.post("/projects/some-other-id/reenrich", json={"overrides": {}})
    assert response.status_code == 400
