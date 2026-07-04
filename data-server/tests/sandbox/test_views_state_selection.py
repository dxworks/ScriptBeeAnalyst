"""State-driven sandbox-view selection in /execute (and /plot).

P5.A: ``/execute`` and ``/plot`` must pick the sandbox-view class from
``graph.merge_state`` — :class:`SetupSandboxView` for PRE_MERGE,
:class:`QuerySandboxView` (the renamed ``MCPSandboxView``) for
FINALIZED.

These tests exercise the view-selection code path through the live
``/execute`` handler via :class:`TestClient` (same fixture pattern as
``test_server_sandbox_injection.py``). The actual class of
``graph_data`` is observed by ``type(graph_data).__name__`` inside the
sandboxed code; attribute-availability differences are observed by
asking for a setup-only attribute and a query-only attribute on each
view and asserting which one resolves.
"""
from __future__ import annotations

import os

import pytest

# Same Supabase-config shim as the sibling sandbox tests.
os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake")
os.environ.setdefault("WORKSPACE_ROOT", "/tmp")

from fastapi.testclient import TestClient  # noqa: E402

from src import server  # noqa: E402
from src.common.kernel import Graph, MergeState  # noqa: E402
from src.filter_rules.store import filter_rule_store  # noqa: E402
from src.graph_store import graph_store  # noqa: E402
from src.sandbox import QuerySandboxView, SetupSandboxView  # noqa: E402


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def pre_merge_client():
    """Inject an empty graph in PRE_MERGE state and yield a TestClient.

    PRE_MERGE is the default ``Graph.merge_state``, but we set it
    explicitly so the test reads as an intent statement.
    """
    project_id = "test-views-pre-merge"
    graph = Graph(project_id=project_id)
    graph.merge_state = MergeState.PRE_MERGE
    graph_store.set(project_id, graph)
    server.current_project_id = project_id
    try:
        yield TestClient(server.app), graph
    finally:
        graph_store.delete(project_id)
        server.current_project_id = None


@pytest.fixture
def finalized_client(monkeypatch):
    """Inject an empty graph in FINALIZED state and yield a TestClient.

    /execute under FINALIZED reaches into ``filter_rule_store`` to
    check for excluded ids, which would otherwise try to talk to
    Supabase — so we stub that to an empty mapping for the duration
    of the test.
    """
    project_id = "test-views-finalized"
    graph = Graph(project_id=project_id)
    graph.merge_state = MergeState.FINALIZED
    graph_store.set(project_id, graph)
    server.current_project_id = project_id

    monkeypatch.setattr(
        filter_rule_store, "excluded_ids_for", lambda _pid: {}
    )

    try:
        yield TestClient(server.app), graph
    finally:
        graph_store.delete(project_id)
        server.current_project_id = None


# ----------------------------------------------------------------------
# PRE_MERGE -> SetupSandboxView
# ----------------------------------------------------------------------
def test_pre_merge_graph_binds_setup_sandbox_view(pre_merge_client):
    """/execute against a PRE_MERGE graph exposes ``graph_data`` as a
    :class:`SetupSandboxView` (narrow setup-stage surface)."""
    client, _ = pre_merge_client
    response = client.post(
        "/execute",
        json={"code": "print(type(graph_data).__name__)"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["output"].strip() == "SetupSandboxView"


def test_pre_merge_view_exposes_setup_surface(pre_merge_client):
    """The setup view exposes the per-source registries and the
    primary entity surfaces — these are the diagnostics the
    setup stage needs."""
    client, _ = pre_merge_client
    response = client.post(
        "/execute",
        json={
            "code": (
                "print(graph_data.git_accounts is not None);"
                " print(graph_data.jira_users is not None);"
                " print(graph_data.github_users is not None);"
                " print(graph_data.commits is not None);"
                " print(graph_data.files is not None);"
                " print(graph_data.issues is not None);"
                " print(graph_data.pull_requests is not None)"
            )
        },
    )
    assert response.status_code == 200, response.text
    lines = response.json()["output"].strip().splitlines()
    assert lines == ["True"] * 7


def test_pre_merge_view_hides_query_only_surface(pre_merge_client):
    """The setup view DOES NOT expose post-finalize attributes like
    ``unified_users`` / ``traits`` / ``classifiers`` — accessing them
    raises :class:`AttributeError` (caught by the /execute traceback
    machinery and surfaced as a 400)."""
    client, _ = pre_merge_client
    response = client.post(
        "/execute",
        json={"code": "graph_data.unified_users"},
    )
    # /execute wraps tracebacks into a 400 JSONResponse — see the
    # endpoint's `except Exception` branch.
    assert response.status_code == 400
    assert "AttributeError" in response.json()["error"]
    assert "unified_users" in response.json()["error"]


@pytest.mark.parametrize(
    "attr",
    ["traits", "classifiers", "relations", "components", "file_metrics"],
)
def test_pre_merge_view_hides_enrichment_attrs(pre_merge_client, attr):
    """Every enrichment-derived attribute that QuerySandboxView exposes
    must be absent on SetupSandboxView."""
    client, _ = pre_merge_client
    response = client.post(
        "/execute",
        json={"code": f"graph_data.{attr}"},
    )
    assert response.status_code == 400
    assert "AttributeError" in response.json()["error"]


def test_pre_merge_summary_helpers_callable(pre_merge_client):
    """``git_summary`` / ``github_summary`` / ``jira_summary`` are
    available on the setup view (counts-only diagnostics)."""
    client, _ = pre_merge_client
    response = client.post(
        "/execute",
        json={
            "code": (
                "print(callable(graph_data.git_summary));"
                " print(callable(graph_data.github_summary));"
                " print(callable(graph_data.jira_summary))"
            )
        },
    )
    assert response.status_code == 200, response.text
    lines = response.json()["output"].strip().splitlines()
    assert lines == ["True"] * 3


# ----------------------------------------------------------------------
# FINALIZED -> QuerySandboxView
# ----------------------------------------------------------------------
def test_finalized_graph_binds_query_sandbox_view(finalized_client):
    """/execute against a FINALIZED graph exposes ``graph_data`` as a
    :class:`QuerySandboxView` (full query-stage surface)."""
    client, _ = finalized_client
    response = client.post(
        "/execute",
        json={"code": "print(type(graph_data).__name__)"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["output"].strip() == "QuerySandboxView"


def test_finalized_view_exposes_unified_users(finalized_client):
    """The query view reaches the full graph surface — including the
    post-finalize ``unified_users`` aggregate. The empty graph carries
    an empty registry but the attribute itself must resolve."""
    client, _ = finalized_client
    response = client.post(
        "/execute",
        json={
            "code": (
                "print(graph_data.unified_users is not None);"
                " print(len(graph_data.unified_users))"
            )
        },
    )
    assert response.status_code == 200, response.text
    lines = response.json()["output"].strip().splitlines()
    assert lines == ["True", "0"]


# ----------------------------------------------------------------------
# Sanity: ``commits`` works on both views
# ----------------------------------------------------------------------
def test_commits_accessor_works_on_setup_view(pre_merge_client):
    client, _ = pre_merge_client
    response = client.post(
        "/execute",
        json={"code": "print(graph_data.commits.all())"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["output"].strip() == "()"


def test_commits_accessor_works_on_query_view(finalized_client):
    client, _ = finalized_client
    response = client.post(
        "/execute",
        json={"code": "print(graph_data.commits.all())"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["output"].strip() == "()"


# ----------------------------------------------------------------------
# Direct view-class smoke checks (no /execute round-trip)
# ----------------------------------------------------------------------
def test_setup_view_direct_construction_smoke():
    g = Graph(project_id="smoke-setup")
    view = SetupSandboxView(g)
    # The four primary entity surfaces hand back the underlying registry.
    assert view.commits is g.commits
    assert view.files is g.files
    assert view.issues is g.issues
    assert view.pull_requests is g.pull_requests
    # Per-source registries.
    assert view.git_accounts is g.git_accounts
    assert view.jira_users is g.jira_users
    assert view.github_users is g.github_users
    # And the per-domain summaries are callable.
    assert callable(view.git_summary)
    assert callable(view.github_summary)
    assert callable(view.jira_summary)


def test_setup_view_does_not_have_query_only_attrs():
    g = Graph(project_id="smoke-setup-2")
    view = SetupSandboxView(g)
    for attr in (
        "unified_users",
        "traits",
        "classifiers",
        "relations",
        "components",
        "file_metrics",
        "tags_for",
        "find_files_with_trait",
        "find_files_with_classifier",
        "cochange_neighbors",
        "overview_as_dict",
    ):
        assert not hasattr(view, attr), (
            f"SetupSandboxView unexpectedly exposes {attr!r}"
        )


def test_query_view_is_the_renamed_mcpsandboxview():
    from src.sandbox import MCPSandboxView

    assert MCPSandboxView is QuerySandboxView
