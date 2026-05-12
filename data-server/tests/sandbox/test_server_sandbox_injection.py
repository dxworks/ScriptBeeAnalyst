"""Smoke test: /execute wraps the loaded Graph in MCPSandboxView.

The full HTTP path (auth + middleware + JSON body) is out of scope —
we exercise the handler-callable shape via :class:`TestClient` AND
verify that the server module wires :class:`MCPSandboxView` into the
exec sandbox (so user code that types ``graph_data.commits.all()``
sees the v2-typed registry rather than the legacy dict).
"""
from __future__ import annotations

import os

import pytest

# Ensure the server module imports cleanly without a real Supabase
# config (same shim as tests/enrichment/test_reenrich_endpoint.py).
os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake")
os.environ.setdefault("WORKSPACE_ROOT", "/tmp")

from fastapi.testclient import TestClient  # noqa: E402

from src import server  # noqa: E402
from src.common.kernel import Graph  # noqa: E402
from src.graph_store import graph_store  # noqa: E402
from src.sandbox import MCPSandboxView  # noqa: E402


PROJECT_ID = "test-sb-inject"


@pytest.fixture
def loaded_client():
    """Stash a real typed :class:`Graph` into the store, return a TestClient.

    We bypass the load endpoint (which needs Supabase) and inject the
    Graph directly. The /execute handler reads it from the store.
    """
    graph = Graph(project_id=PROJECT_ID)
    graph_store.set(PROJECT_ID, graph)
    server.current_project_id = PROJECT_ID
    try:
        yield TestClient(server.app)
    finally:
        graph_store.delete(PROJECT_ID)
        server.current_project_id = None


def test_execute_exposes_mcpsandboxview_as_graph_data(loaded_client):
    response = loaded_client.post(
        "/execute",
        json={
            "code": (
                "print(type(graph_data).__name__);"
                " print(graph_data.project_id);"
                " print(len(graph_data.commits))"
            )
        },
    )
    assert response.status_code == 200, response.text
    out = response.json()["output"]
    assert "MCPSandboxView" in out
    assert PROJECT_ID in out
    assert "0" in out  # 0 commits in the empty graph


def test_execute_exposes_helper_functions(loaded_client):
    response = loaded_client.post(
        "/execute",
        json={
            "code": (
                "print(callable(commit_issues));"
                " print(callable(issue_commits));"
                " print(callable(pr_commits));"
                " print(callable(find_files_with_trait));"
                " print(callable(cochange_neighbors));"
                " print(callable(overview_as_dict))"
            )
        },
    )
    assert response.status_code == 200, response.text
    # Every helper must be callable in the sandbox.
    lines = response.json()["output"].strip().splitlines()
    assert lines == ["True"] * 6


def test_execute_graph_data_supports_legacy_attribute_shape(loaded_client):
    """The sandbox view must read like ``graph_data.commits.all()`` —
    that is the canonical Chunk-9 surface (plan §11 row 1).
    """
    response = loaded_client.post(
        "/execute",
        json={"code": "print(graph_data.commits.all())"},
    )
    assert response.status_code == 200, response.text
    # Empty graph -> empty tuple.
    assert response.json()["output"].strip() == "()"


def test_execute_with_no_project_loaded_yields_none_graph_data():
    """When no project is loaded the sandbox view is ``None`` —
    agents call ``get_project_status`` first per the docs."""
    server.current_project_id = None
    client = TestClient(server.app)
    response = client.post(
        "/execute",
        json={"code": "print(graph_data is None)"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["output"].strip() == "True"


def test_mcpsandboxview_construction_smoke():
    """Bare smoke: constructing the view over an empty Graph doesn't
    blow up and exposes the four named registries."""
    g = Graph(project_id="smoke")
    view = MCPSandboxView(g)
    assert view.commits is g.commits
    assert view.files is g.files
    assert view.issues is g.issues
    assert view.pull_requests is g.pull_requests
