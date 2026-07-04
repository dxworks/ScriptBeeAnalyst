"""End-to-end smoke: generated resolver methods work through /execute.

Builds a 1-commit, 1-account graph; hits ``/execute`` with the short-form
traversal ``graph_data.commits.all()[0].author(graph_data).email`` and
asserts the response.

This locks the agent-facing contract from ``NiceCoderefsWriting.md``:
inside the MCPSandboxView the same resolver methods the kernel installs
on every Entity are callable on instances and resolve through the
sandbox view (which forwards to the underlying typed ``Graph``).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake")
os.environ.setdefault("WORKSPACE_ROOT", "/tmp")

from fastapi.testclient import TestClient  # noqa: E402

from src import server  # noqa: E402
from src.common.domains.git.models import Commit, GitAccount, GitProject  # noqa: E402
from src.common.kernel import Graph  # noqa: E402
from src.common.people import SourceKind  # noqa: E402
from src.graph_store import graph_store  # noqa: E402


PROJECT_ID = "test-resolvers-through-view"
UTC = timezone.utc


@pytest.fixture
def loaded_client():
    """One-commit one-account graph published to the graph store."""
    graph = Graph(project_id=PROJECT_ID)
    project = GitProject(id="gp1", name="zep", source=SourceKind.GIT)
    graph.add_project(project)
    alice = GitAccount(
        id=GitAccount.make_id("Alice", "a@x"),
        name="Alice",
        email="a@x",
        project_ref=project.ref(),
    )
    graph.git_accounts.add(alice)
    when = datetime(2024, 1, 1, tzinfo=UTC)
    commit = Commit(
        id="zep:abc",
        sha="abc",
        project_ref=project.ref(),
        message="initial",
        author_date=when,
        committer_date=when,
        author_ref=alice.ref(),
        committer_ref=alice.ref(),
    )
    graph.commits.add(commit)

    graph_store.set(PROJECT_ID, graph)
    server.current_project_id = PROJECT_ID
    try:
        yield TestClient(server.app)
    finally:
        graph_store.delete(PROJECT_ID)
        server.current_project_id = None


def test_short_form_resolver_through_execute(loaded_client):
    response = loaded_client.post(
        "/execute",
        json={
            "code": (
                "c = graph_data.commits.all()[0]\n"
                "print(c.author(graph_data).email)\n"
                "print(c.committer(graph_data).name)\n"
                "print(c.project(graph_data).name)\n"
                "print(c.parents(graph_data))"
            )
        },
    )
    assert response.status_code == 200, response.text
    lines = response.json()["output"].strip().splitlines()
    assert lines == ["a@x", "Alice", "zep", "[]"]


def test_unresolved_ref_returns_none_through_execute(loaded_client):
    """A ref pointing at an id that isn't in the graph resolves to None,
    not an exception — matches the kernel contract."""
    response = loaded_client.post(
        "/execute",
        json={
            "code": (
                "from src.common.kernel.ref import EntityRef\n"
                "from src.common.kernel.kinds import EntityKind\n"
                "from src.common.domains.git.models import Commit\n"
                "from datetime import datetime, timezone\n"
                "ghost = Commit(id='zep:ghost', sha='ghost',\n"
                "    project_ref=EntityRef(kind=EntityKind.PROJECT, id='gp1'),\n"
                "    message='x', author_date=datetime.now(timezone.utc),\n"
                "    committer_date=datetime.now(timezone.utc),\n"
                "    author_ref=EntityRef(kind=EntityKind.GIT_ACCOUNT, id='nobody'),\n"
                "    committer_ref=EntityRef(kind=EntityKind.GIT_ACCOUNT, id='nobody'))\n"
                "print(ghost.author(graph_data) is None)"
            )
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["output"].strip() == "True"
