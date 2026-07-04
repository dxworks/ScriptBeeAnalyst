"""Shared fixtures for smart-merge endpoint integration tests (Chunk 19).

Provides:

* A typed v2 :class:`Graph` builder seeded with git + jira + github
  accounts whose names are designed to produce a single high-confidence
  smart-merge cluster (Alice in three sources).
* An in-memory :class:`SmartMergeRepository` so tests don't reach for
  Supabase.
* A :func:`patched_endpoints` fixture that stubs the
  :class:`SupabaseSmartMergeRepository` factory inside ``src.server``
  with the in-memory repo for the duration of the test.

The fixtures stay deliberately minimal — each test composes the bits it
needs. The :class:`Graph` shape matches the slot expectations of
:func:`src.smart_merge.identity_extractor.extract_all_identities`:

* ``GitAccount.id`` ==  ``"{name} <{email}>"`` (from :meth:`GitAccount.make_id`)
* ``GitHubUser.id`` == the user's URL
* ``JiraUser.id``   == the user's link URL

Phase-1 wire compatibility: persisted ``user_identity_mappings.source_key``
rows from the v1 schema replay verbatim against these shapes.
"""
from __future__ import annotations

import os
from typing import Iterable, List

# Module-import-time env shims — same approach the sandbox tests use.
os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake")
os.environ.setdefault("WORKSPACE_ROOT", "/tmp")

import pytest  # noqa: E402

from src.common.domains.git.models import GitAccount, GitProject  # noqa: E402
from src.common.domains.github.models import GitHubProject, GitHubUser  # noqa: E402
from src.common.domains.jira.models import JiraProject, JiraUser  # noqa: E402
from src.common.kernel import Graph  # noqa: E402
from src.common.people import SourceKind  # noqa: E402
from src.smart_merge.repository import SmartMergeRepository  # noqa: E402
from src.smart_merge.types import RejectedPair, UserMapping  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory repository (no Supabase calls)
# ---------------------------------------------------------------------------
class InMemorySmartMergeRepository(SmartMergeRepository):
    """Replaces the Supabase-backed repo for endpoint integration tests.

    Same surface as :class:`SupabaseSmartMergeRepository` — the smart-merge
    engine + endpoints code path is exercised end-to-end, only the
    persistence layer is in-memory. ``delete_all_*`` returns counts that
    match what the real repo would return.
    """

    def __init__(self) -> None:
        self._rejected: List[RejectedPair] = []
        self._mappings: List[UserMapping] = []

    def get_rejected_similarities(self, project_id: str) -> List[RejectedPair]:
        return [p for p in self._rejected if p.project_id == project_id]

    def add_rejected_similarities(
        self, project_id: str, pairs: Iterable[RejectedPair],
    ) -> None:
        self._rejected.extend(pairs)

    def get_user_mappings(self, project_id: str) -> List[UserMapping]:
        return list(self._mappings)

    def upsert_user_mapping(self, mapping: UserMapping, project_id: str) -> None:
        self._mappings = [m for m in self._mappings if m.unified_user_id != mapping.unified_user_id]
        self._mappings.append(mapping)

    def delete_user_mapping(self, project_id: str, unified_user_id: str) -> None:
        self._mappings = [m for m in self._mappings if m.unified_user_id != unified_user_id]

    def delete_all_user_mappings(self, project_id: str) -> int:
        count = len(self._mappings)
        self._mappings = []
        return count

    def delete_all_rejected_similarities(self, project_id: str) -> int:
        count = len(self._rejected)
        self._rejected = []
        return count


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------
def _build_git_project(graph: Graph) -> GitProject:
    proj = GitProject(id="gp:demo", name="demo", source=SourceKind.GIT)
    graph.add_project(proj)
    return proj


def _build_jira_project(graph: Graph) -> JiraProject:
    proj = JiraProject(id="jp:demo", name="demo-jira", source=SourceKind.JIRA)
    graph.add_project(proj)
    return proj


def _build_github_project(graph: Graph) -> GitHubProject:
    proj = GitHubProject(id="ghp:demo", name="demo-github", source=SourceKind.GITHUB)
    graph.add_project(proj)
    return proj


#: The distinctive Alice display name. Four tokens give the token-similarity
#: engine enough "evidence" (strength=4 ≥ MIN_CLUSTER_AVG_STRENGTH=4.0)
#: that the resulting triangle passes the tight-cluster validation in
#: :meth:`AuthorSmartMergeEngine._extract_tight_clusters`. Shorter
#: names produce strength 2-3 and the cluster gets pruned away — the
#: engine is intentionally conservative.
ALICE_NAME = "Alice Marie Catherine Example"
ALICE_EMAIL = "alice.marie.catherine@example.com"
ALICE_LOGIN = "alice-marie-catherine-example"


def build_three_source_graph(project_id: str = "test-project") -> Graph:
    """Build a v2 :class:`Graph` with one cluster of three "Alice" identities
    spread across git/github/jira, plus one isolated "Bob" identity.

    The Alice cluster is designed to produce a single high-confidence
    smart-merge suggestion (matching distinctive names + matching
    login). Bob has a single git account; he never matches anyone else.
    """
    graph = Graph(project_id=project_id)
    git_proj = _build_git_project(graph)
    jira_proj = _build_jira_project(graph)
    github_proj = _build_github_project(graph)

    # --- Alice (the cluster) ---
    alice_git = GitAccount(
        id=GitAccount.make_id(ALICE_NAME, ALICE_EMAIL),
        name=ALICE_NAME,
        email=ALICE_EMAIL,
        project_ref=git_proj.ref(),
    )
    graph.git_accounts.add(alice_git)

    alice_github = GitHubUser(
        id=f"https://github.com/{ALICE_LOGIN}",
        name=ALICE_NAME,
        login=ALICE_LOGIN,
        url=f"https://github.com/{ALICE_LOGIN}",
        project_ref=github_proj.ref(),
    )
    graph.github_users.add(alice_github)

    alice_jira = JiraUser(
        id=f"https://jira.example.com/{ALICE_LOGIN}",
        name=ALICE_NAME,
        key=ALICE_LOGIN,
        link=f"https://jira.example.com/{ALICE_LOGIN}",
        project_ref=jira_proj.ref(),
    )
    graph.jira_users.add(alice_jira)

    # --- Bob (isolated) ---
    bob_git = GitAccount(
        id=GitAccount.make_id("Bob Stranger", "bob@example.com"),
        name="Bob Stranger",
        email="bob@example.com",
        project_ref=git_proj.ref(),
    )
    graph.git_accounts.add(bob_git)

    return graph


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def project_id() -> str:
    return "test-smart-merge-project"


@pytest.fixture
def three_source_graph(project_id: str) -> Graph:
    return build_three_source_graph(project_id)


@pytest.fixture
def in_memory_repo() -> InMemorySmartMergeRepository:
    return InMemorySmartMergeRepository()


@pytest.fixture
def patched_server(monkeypatch, in_memory_repo, three_source_graph, project_id):
    """Wire the in-memory repo + the typed Graph into ``src.server``.

    Returns a :class:`TestClient`. Cleans up the graph_store + the
    smart_merge state_store on teardown.
    """
    # Late imports — env shims at module top must be set first.
    from fastapi.testclient import TestClient
    from src import server
    from src.graph_store import graph_store
    from src.smart_merge.state_store import smart_merge_state_store

    # Force every endpoint's repo factory to hand back the in-memory repo.
    monkeypatch.setattr(server, "SupabaseSmartMergeRepository", lambda: in_memory_repo)

    # Stash the graph + reset smart-merge state.
    graph_store.set(project_id, three_source_graph)
    smart_merge_state_store.reset(project_id)
    server.current_project_id = project_id
    server.current_project_name = "test"
    server.current_user_id = "test-user"

    try:
        yield TestClient(server.app)
    finally:
        graph_store.delete(project_id)
        smart_merge_state_store.delete(project_id)
        server.current_project_id = None
        server.current_project_name = None
        server.current_user_id = None
