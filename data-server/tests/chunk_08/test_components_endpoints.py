"""B3 — REST endpoints for the components page.

Covers:

* ``GET /projects/{id}/components`` — registry rollup with summed sum_nloc.
* ``GET /projects/{id}/components/files`` — flat per-file rows with the
  component name + loc joined in.
* ``PUT /projects/{id}/component-mapping`` — happy path (persist + rebuild),
  malformed body (400), null body (clears column to SQL NULL).

The Supabase service client is monkey-patched at the import sites the
endpoints actually touch (``src.server.get_service_client`` for the PUT
write, ``src.processor.get_service_client`` for the rebuild's fetch). The
rebuild itself is stubbed via ``v2_processor.build_graph`` so the test
doesn't reach for Supabase Storage / the real pipeline.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake")
os.environ.setdefault("WORKSPACE_ROOT", "/tmp")

from fastapi.testclient import TestClient  # noqa: E402

from src import processor as v2_processor  # noqa: E402
from src import server  # noqa: E402
from src.common.domains.components.models import Component  # noqa: E402
from src.common.domains.git.models import File, GitAccount, GitProject  # noqa: E402
from src.common.domains.metrics_lizard.models import (  # noqa: E402
    FileMetric,
    LizardMetricsProject,
)
from src.common.kernel import Graph  # noqa: E402
from src.common.people import SourceKind  # noqa: E402
from src.graph_store import graph_store  # noqa: E402


PROJECT_ID = "test-components-project"


# ---------------------------------------------------------------------------
# Supabase fake — captures what the PUT writes
# ---------------------------------------------------------------------------
class _RecorderQuery:
    def __init__(self, recorder: Dict[str, Any]):
        self._recorder = recorder

    def update(self, payload: Dict[str, Any]):
        self._recorder["update"] = payload
        return self

    def eq(self, field: str, value: str):
        self._recorder.setdefault("eq", []).append((field, value))
        return self

    def select(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        # Update calls return data=[updated_row]; we only need a truthy
        # response that doesn't raise. The endpoint ignores the body.
        class _Resp:
            data = [{"id": "ok"}]
        return _Resp()


class _RecorderClient:
    def __init__(self, recorder: Dict[str, Any]):
        self._recorder = recorder

    def table(self, name: str):
        self._recorder["table"] = name
        return _RecorderQuery(self._recorder)


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------
def _build_graph_with_two_components() -> Graph:
    """Build a typed Graph with two components and Lizard sum_nloc rows.

    Component layout:
      core   (path_prefix=src/core/): files a.py (loc=100), b.py (loc=200) → total=300
      ui     (path_prefix=src/ui/):   file c.py (loc=50)                    → total=50
      (one file d.py has NO Lizard row — contributes 0 to its component)
      core also picks up d.py to exercise the "missing loc" path
    """
    graph = Graph(project_id=PROJECT_ID)

    git_proj = GitProject(id="gp:demo", name="demo", source=SourceKind.GIT)
    graph.add_project(git_proj)

    liz_proj = LizardMetricsProject(
        id="lp:demo", name="demo-lizard", source=SourceKind.LIZARD
    )
    graph.add_project(liz_proj)

    files = []
    for path, loc in [
        ("src/core/a.py", 100.0),
        ("src/core/b.py", 200.0),
        ("src/ui/c.py", 50.0),
        ("src/core/d.py", None),  # No Lizard row
    ]:
        f = File(
            id=File.make_id("demo", path),
            project_ref=git_proj.ref(),
            path=path,
            extension=File.derive_extension(path),
        )
        graph.files.add(f)
        files.append(f)
        if loc is not None:
            fm = FileMetric(
                id=FileMetric.make_id(f.id, "sum_nloc"),
                project_ref=liz_proj.ref(),
                file_ref=f.ref(),
                metric_name="sum_nloc",
                value=loc,
            )
            graph.file_metrics.add(fm)

    # Components: core owns a.py, b.py, d.py; ui owns c.py.
    core = Component(
        id="core",
        name="core",
        path_prefix="src/core/",
        file_refs=[files[0].ref(), files[1].ref(), files[3].ref()],
        project_ref=git_proj.ref(),
    )
    ui = Component(
        id="ui",
        name="ui",
        path_prefix="src/ui/",
        file_refs=[files[2].ref()],
        project_ref=git_proj.ref(),
    )
    graph.components.add(core)
    graph.components.add(ui)
    return graph


def _build_empty_graph() -> Graph:
    """Graph with a project but zero components / files."""
    graph = Graph(project_id=PROJECT_ID)
    git_proj = GitProject(id="gp:empty", name="empty", source=SourceKind.GIT)
    graph.add_project(git_proj)
    return graph


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def client_with_graph():
    """Inject the two-component graph into graph_store, hand back a TestClient."""
    graph = _build_graph_with_two_components()
    graph_store.set(PROJECT_ID, graph)
    server.current_project_id = PROJECT_ID
    try:
        yield TestClient(server.app)
    finally:
        graph_store.delete(PROJECT_ID)
        server.current_project_id = None


@pytest.fixture
def client_with_empty_graph():
    graph = _build_empty_graph()
    graph_store.set(PROJECT_ID, graph)
    server.current_project_id = PROJECT_ID
    try:
        yield TestClient(server.app)
    finally:
        graph_store.delete(PROJECT_ID)
        server.current_project_id = None


@pytest.fixture
def client_no_graph():
    """No graph loaded → endpoints should 400."""
    # Defensive: ensure no leftover graph from another test
    graph_store.delete(PROJECT_ID)
    server.current_project_id = None
    try:
        yield TestClient(server.app)
    finally:
        graph_store.delete(PROJECT_ID)
        server.current_project_id = None


# ---------------------------------------------------------------------------
# GET /projects/{id}/components
# ---------------------------------------------------------------------------
class TestGetComponents:
    def test_happy_path_returns_rolled_up_components(self, client_with_graph):
        response = client_with_graph.get(f"/projects/{PROJECT_ID}/components")
        assert response.status_code == 200, response.text
        body = response.json()
        assert isinstance(body, list)
        assert len(body) == 2

        by_name = {row["name"]: row for row in body}
        core = by_name["core"]
        assert core["path_prefix"] == "src/core/"
        assert core["file_count"] == 3
        assert core["total_loc"] == 300.0  # 100 + 200 + (d.py missing = 0)
        assert core["color"] is None

        ui = by_name["ui"]
        assert ui["path_prefix"] == "src/ui/"
        assert ui["file_count"] == 1
        assert ui["total_loc"] == 50.0
        assert ui["color"] is None

    def test_empty_components_returns_empty_list(self, client_with_empty_graph):
        response = client_with_empty_graph.get(f"/projects/{PROJECT_ID}/components")
        assert response.status_code == 200, response.text
        assert response.json() == []

    def test_not_loaded_returns_400(self, client_no_graph):
        response = client_no_graph.get(f"/projects/{PROJECT_ID}/components")
        assert response.status_code == 400
        assert "not loaded" in response.json()["error"].lower()


# ---------------------------------------------------------------------------
# GET /projects/{id}/components/files
# ---------------------------------------------------------------------------
class TestGetComponentFiles:
    def test_happy_path_flat_rows_with_loc_and_component(self, client_with_graph):
        response = client_with_graph.get(f"/projects/{PROJECT_ID}/components/files")
        assert response.status_code == 200, response.text
        body = response.json()
        assert isinstance(body, list)
        assert len(body) == 4

        by_path = {row["path"]: row for row in body}
        assert by_path["src/core/a.py"]["loc"] == 100.0
        assert by_path["src/core/a.py"]["component_name"] == "core"
        assert by_path["src/core/b.py"]["loc"] == 200.0
        assert by_path["src/core/b.py"]["component_name"] == "core"
        assert by_path["src/ui/c.py"]["loc"] == 50.0
        assert by_path["src/ui/c.py"]["component_name"] == "ui"
        # d.py has no Lizard row → loc null; still owned by core
        assert by_path["src/core/d.py"]["loc"] is None
        assert by_path["src/core/d.py"]["component_name"] == "core"

    def test_empty_project_returns_empty_list(self, client_with_empty_graph):
        response = client_with_empty_graph.get(
            f"/projects/{PROJECT_ID}/components/files"
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_not_loaded_returns_400(self, client_no_graph):
        response = client_no_graph.get(f"/projects/{PROJECT_ID}/components/files")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# PUT /projects/{id}/component-mapping
# ---------------------------------------------------------------------------
class TestPutComponentMapping:
    def _patched_writes(self, monkeypatch, recorder: Dict[str, Any]):
        """Patch the Supabase write site + stub build_graph as a no-op."""
        monkeypatch.setattr(
            server, "get_service_client",
            lambda: _RecorderClient(recorder),
        )

        def _fake_build(project_id, *args, **kwargs):
            from src.enrichment.pipeline import PipelineResult
            graph = _build_graph_with_two_components()
            return graph, PipelineResult(builders_run=[], metrics_run=[], errors=[])

        monkeypatch.setattr(v2_processor, "build_graph", _fake_build)

    def test_happy_path_persists_and_rebuilds(self, monkeypatch):
        recorder: Dict[str, Any] = {}
        self._patched_writes(monkeypatch, recorder)

        client = TestClient(server.app)
        payload = {
            "core": {"path_prefix": "src/core/", "extra_paths": ["lib/core/"]},
            "ui": {"path_prefix": "src/ui/"},
        }
        try:
            response = client.put(
                f"/projects/{PROJECT_ID}/component-mapping",
                json=payload,
            )
        finally:
            graph_store.delete(PROJECT_ID)
            server.current_project_id = None

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        assert body["cleared"] is False
        assert body["component_count"] == 2

        # Persistence side effect: update was called with the payload.
        assert recorder["table"] == "projects"
        assert recorder["update"] == {"component_mapping": payload}
        assert ("id", PROJECT_ID) in recorder["eq"]

    def test_null_body_clears_to_sql_null(self, monkeypatch):
        recorder: Dict[str, Any] = {}
        self._patched_writes(monkeypatch, recorder)

        client = TestClient(server.app)
        try:
            response = client.put(
                f"/projects/{PROJECT_ID}/component-mapping",
                json=None,
            )
        finally:
            graph_store.delete(PROJECT_ID)
            server.current_project_id = None

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        assert body["cleared"] is True
        # SQL NULL on the wire == Python None in the update payload.
        assert recorder["update"] == {"component_mapping": None}

    def test_empty_object_body_also_clears(self, monkeypatch):
        recorder: Dict[str, Any] = {}
        self._patched_writes(monkeypatch, recorder)

        client = TestClient(server.app)
        try:
            response = client.put(
                f"/projects/{PROJECT_ID}/component-mapping",
                json={},
            )
        finally:
            graph_store.delete(PROJECT_ID)
            server.current_project_id = None

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["cleared"] is True
        assert recorder["update"] == {"component_mapping": None}

    def test_malformed_body_top_level_list_returns_400(self, monkeypatch):
        recorder: Dict[str, Any] = {}
        # Patch the Supabase site so a leaked write would be visible.
        monkeypatch.setattr(
            server, "get_service_client",
            lambda: _RecorderClient(recorder),
        )

        client = TestClient(server.app)
        response = client.put(
            f"/projects/{PROJECT_ID}/component-mapping",
            json=["not", "an", "object"],
        )
        assert response.status_code == 400
        assert "object" in response.json()["error"].lower()
        # No write happened.
        assert "update" not in recorder

    def test_malformed_entries_return_400(self, monkeypatch):
        """Every entry is invalid (no path_prefix) → parser drops them all
        → endpoint surfaces a 400 rather than persisting an effectively-empty
        mapping. Without this, the operator's bad payload silently no-ops."""
        recorder: Dict[str, Any] = {}
        monkeypatch.setattr(
            server, "get_service_client",
            lambda: _RecorderClient(recorder),
        )

        client = TestClient(server.app)
        response = client.put(
            f"/projects/{PROJECT_ID}/component-mapping",
            # All entries are missing path_prefix → all dropped → empty.
            json={"core": {"extra_paths": ["lib/core/"]}, "ui": {}},
        )
        assert response.status_code == 400
        assert "no valid component" in response.json()["error"].lower()
        assert "update" not in recorder

    def test_supabase_write_failure_returns_500_no_rebuild(self, monkeypatch):
        rebuild_calls = []

        def _boom_client():
            raise RuntimeError("supabase down")

        monkeypatch.setattr(server, "get_service_client", _boom_client)

        def _track_build(project_id, *args, **kwargs):
            rebuild_calls.append(project_id)
            raise AssertionError("rebuild should not run when write fails")

        monkeypatch.setattr(v2_processor, "build_graph", _track_build)

        client = TestClient(server.app)
        response = client.put(
            f"/projects/{PROJECT_ID}/component-mapping",
            json={"core": {"path_prefix": "src/"}},
        )
        assert response.status_code == 500
        assert "persist" in response.json()["error"].lower()
        assert rebuild_calls == []

    def test_rebuild_failure_returns_500_but_keeps_persistence(self, monkeypatch):
        recorder: Dict[str, Any] = {}
        monkeypatch.setattr(
            server, "get_service_client",
            lambda: _RecorderClient(recorder),
        )

        def _broken_build(project_id, *args, **kwargs):
            raise RuntimeError("pipeline blew up")

        monkeypatch.setattr(v2_processor, "build_graph", _broken_build)

        client = TestClient(server.app)
        response = client.put(
            f"/projects/{PROJECT_ID}/component-mapping",
            json={"core": {"path_prefix": "src/"}},
        )
        assert response.status_code == 500
        body = response.json()
        assert body.get("mapping_persisted") is True
        assert "rebuild" in body["error"].lower()
        # Write happened before the failed rebuild.
        assert recorder["update"] == {"component_mapping": {"core": {"path_prefix": "src/"}}}
