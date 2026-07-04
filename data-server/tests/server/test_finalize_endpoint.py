"""Integration tests for ``POST /projects/{id}/finalize`` (task P4.A).

UnifiedUsers redesign §G — finalize endpoint. These tests exercise the
endpoint via :class:`fastapi.testclient.TestClient`, with the typed v2
:class:`Graph` injected into ``graph_store`` and the Supabase
``projects`` table writes captured by an in-process stub. The Supabase
network layer is never touched.

Coverage:

* Happy path on a ``PRE_MERGE`` graph: returns 200, flips state to
  ``FINALIZED`` in memory, captures the rebind stats + Phase-B emission
  counts, and writes the new state + frozen config to the stub
  Supabase client.
* Idempotency: a second /finalize call on the same project returns 409
  with ``error == "project_finalized"``.
* Missing graph: /finalize on an unloaded project returns 404.
* Half-finalize state visibility: the stub Supabase client records
  exactly one ``projects`` write with ``merge_state == 'FINALIZED'`` —
  the test asserts the payload directly rather than round-tripping
  through a live Supabase mock (kept minimal per the P4.A brief).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

# Module-import-time env shims — same approach the rest of the test
# suite uses (the server module reads these at import time via
# ``src.config``).
os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake")
os.environ.setdefault("WORKSPACE_ROOT", "/tmp")

import pytest  # noqa: E402

from src.common.domains.git.models import (  # noqa: E402
    Commit,
    GitAccount,
    GitProject,
)
from src.common.domains.github.models import (  # noqa: E402
    GitHubProject,
    GitHubUser,
    PullRequest,
)
from src.common.kernel import EntityKind, Graph, MergeState  # noqa: E402
from src.common.people import SourceKind  # noqa: E402


# ---------------------------------------------------------------------------
# Stub Supabase client — captures every ``.table(...).update(...)`` call so
# the test body can introspect the persisted payload. The real client is
# never instantiated.
# ---------------------------------------------------------------------------
class _StubExec:
    def __init__(self, recorder: list, payload: dict, eq_filter: tuple) -> None:
        self._recorder = recorder
        self._payload = payload
        self._eq_filter = eq_filter

    def execute(self):
        self._recorder.append({
            "filter": self._eq_filter,
            "payload": dict(self._payload),
        })
        # Mimic the supabase-py return shape: an object with ``.data``.
        class _Resp:
            data: list = []
        return _Resp()


class _StubUpdate:
    def __init__(self, recorder: list, payload: dict) -> None:
        self._recorder = recorder
        self._payload = payload

    def eq(self, col: str, val):
        return _StubExec(self._recorder, self._payload, (col, val))


class _StubTable:
    def __init__(self, recorder: list, name: str) -> None:
        self._recorder = recorder
        self._name = name

    def update(self, payload: dict) -> _StubUpdate:
        return _StubUpdate(self._recorder, payload)


class StubSupabaseClient:
    """Records writes to ``projects`` (and any other tables touched).

    The endpoint only does ``.table("projects").update(...).eq(...).execute()``,
    so we hand back chainable stubs that capture the final ``.execute()``
    payload into ``self.writes``.
    """

    def __init__(self) -> None:
        self.writes: list = []

    def table(self, name: str) -> _StubTable:
        return _StubTable(self.writes, name)


# ---------------------------------------------------------------------------
# Graph fixture — a small but realistic PRE_MERGE graph with role-typed
# account refs that the rebind pass can rewrite.
# ---------------------------------------------------------------------------
ALICE_NAME = "Alice Example"
ALICE_EMAIL = "alice@example.com"
ALICE_LOGIN = "alice-example"
BOB_NAME = "Bob Stranger"
BOB_EMAIL = "bob@example.com"


def _build_finalize_fixture_graph(project_id: str) -> Graph:
    """Build a PRE_MERGE graph with two git accounts (Alice + Bob) and
    one github user, plus a commit and a PR carrying role-typed refs.

    No pre-existing :class:`UnifiedUser` — the rebind pass auto-creates
    singletons for every orphan account.
    """
    graph = Graph(project_id=project_id)

    git_proj = GitProject(id="gp:demo", name="demo-git", source=SourceKind.GIT)
    graph.add_project(git_proj)
    github_proj = GitHubProject(
        id="ghp:demo", name="demo-github", source=SourceKind.GITHUB
    )
    graph.add_project(github_proj)

    alice_git = GitAccount(
        id=GitAccount.make_id(ALICE_NAME, ALICE_EMAIL),
        name=ALICE_NAME,
        email=ALICE_EMAIL,
        project_ref=git_proj.ref(),
    )
    graph.git_accounts.add(alice_git)

    bob_git = GitAccount(
        id=GitAccount.make_id(BOB_NAME, BOB_EMAIL),
        name=BOB_NAME,
        email=BOB_EMAIL,
        project_ref=git_proj.ref(),
    )
    graph.git_accounts.add(bob_git)

    alice_github = GitHubUser(
        id=f"https://github.com/{ALICE_LOGIN}",
        name=ALICE_NAME,
        login=ALICE_LOGIN,
        url=f"https://github.com/{ALICE_LOGIN}",
        project_ref=github_proj.ref(),
    )
    graph.github_users.add(alice_github)

    now = datetime.now(timezone.utc)
    commit_1 = Commit(
        id=Commit.make_id("demo-git", "sha1"),
        project_ref=git_proj.ref(),
        sha="sha1",
        message="first commit",
        author_date=now,
        committer_date=now,
        author_ref=alice_git.ref(),
        committer_ref=alice_git.ref(),
    )
    graph.commits.add(commit_1)

    commit_2 = Commit(
        id=Commit.make_id("demo-git", "sha2"),
        project_ref=git_proj.ref(),
        sha="sha2",
        message="second commit",
        author_date=now,
        committer_date=now,
        author_ref=bob_git.ref(),
        committer_ref=alice_git.ref(),
    )
    graph.commits.add(commit_2)

    pr = PullRequest(
        id=PullRequest.make_id(1),
        project_ref=github_proj.ref(),
        number=1,
        title="PR",
        state="merged",
        created_at=now,
        author_ref=alice_github.ref(),
        merged_by_ref=alice_github.ref(),
    )
    graph.pull_requests.add(pr)

    return graph


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def project_id() -> str:
    return "test-finalize-project"


@pytest.fixture
def pre_merge_graph(project_id: str) -> Graph:
    return _build_finalize_fixture_graph(project_id)


@pytest.fixture
def stub_supabase() -> StubSupabaseClient:
    return StubSupabaseClient()


@pytest.fixture
def patched_server(monkeypatch, stub_supabase, pre_merge_graph, project_id, tmp_path):
    """Wire the stub Supabase client + the typed Graph into ``src.server``.

    Returns a :class:`TestClient`. Cleans up the graph_store on teardown.
    """
    # Late imports — env shims at module top must be set first.
    from fastapi.testclient import TestClient
    from src import server
    from src import processor as v2_processor
    from src.graph_store import graph_store
    from src.smart_merge.state_store import smart_merge_state_store

    monkeypatch.setattr(server, "get_service_client", lambda: stub_supabase)

    # Redirect pickle dumps to a tmp dir so the test doesn't pollute
    # /tmp/pickles/. ``save_graph_to_disk`` calls ``_project_pickle_dir``
    # then ``store.write_registry`` + ``store.meta_write`` — both are
    # fine with a tmp directory.
    pickle_dir = tmp_path / "pickles"
    pickle_dir.mkdir()
    monkeypatch.setattr(
        v2_processor,
        "_project_pickle_dir",
        lambda pid: pickle_dir / pid,
    )

    graph_store.set(project_id, pre_merge_graph)
    smart_merge_state_store.reset(project_id)
    server.current_project_id = project_id
    server.current_project_name = "test-finalize"

    try:
        yield TestClient(server.app)
    finally:
        graph_store.delete(project_id)
        smart_merge_state_store.delete(project_id)
        server.current_project_id = None
        server.current_project_name = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestFinalizeHappyPath:
    """Successful PRE_MERGE → FINALIZED transition."""

    def test_returns_200_and_flips_state(
        self, patched_server, project_id, stub_supabase, pre_merge_graph
    ):
        # Sanity: precondition.
        assert pre_merge_graph.merge_state == MergeState.PRE_MERGE

        response = patched_server.post(f"/projects/{project_id}/finalize")
        assert response.status_code == 200, response.text
        body = response.json()

        # Response shape.
        assert body["merge_state"] == MergeState.FINALIZED.value
        # Two orphan git accounts + one orphan github user → 3 singleton UUs.
        assert body["unified_users_created"] == 3
        # Refs rewritten: commit_1 (author + committer = 2) + commit_2
        # (author + committer = 2) + PR (author + merged_by = 2) = 6.
        assert body["refs_rewritten"] == 6
        # Phase-B counts surfaced (exact values are catalog-dependent; we
        # assert they're present and non-negative — phase B may emit zero
        # on a tiny fixture without enough signal for the metric thresholds).
        assert "phase_b_relations_built" in body
        assert "phase_b_traits_emitted" in body
        assert "phase_b_classifiers_emitted" in body
        assert isinstance(body["phase_b_relations_built"], int)
        assert isinstance(body["phase_b_traits_emitted"], int)
        assert isinstance(body["phase_b_classifiers_emitted"], int)
        assert "duration_ms" in body
        assert body["duration_ms"] >= 0

    def test_in_memory_graph_is_finalized_and_refs_rebound(
        self, patched_server, project_id, pre_merge_graph
    ):
        # Sanity: precondition refs point at GIT_ACCOUNT / GITHUB_USER.
        commit = next(iter(pre_merge_graph.commits.all()))
        assert commit.author_ref.kind == EntityKind.GIT_ACCOUNT

        response = patched_server.post(f"/projects/{project_id}/finalize")
        assert response.status_code == 200

        # In-memory state.
        assert pre_merge_graph.merge_state == MergeState.FINALIZED
        # Every role ref now targets UNIFIED_USER.
        for c in pre_merge_graph.commits.all():
            assert c.author_ref.kind == EntityKind.UNIFIED_USER
            assert c.committer_ref.kind == EntityKind.UNIFIED_USER
        for pr in pre_merge_graph.pull_requests.all():
            assert pr.author_ref.kind == EntityKind.UNIFIED_USER
            assert pr.merged_by_ref.kind == EntityKind.UNIFIED_USER

    def test_persists_merge_state_and_frozen_config_to_supabase(
        self, patched_server, project_id, stub_supabase
    ):
        response = patched_server.post(f"/projects/{project_id}/finalize")
        assert response.status_code == 200

        # Exactly one write to projects — the finalize Supabase persist.
        assert len(stub_supabase.writes) == 1
        write = stub_supabase.writes[0]
        assert write["filter"] == ("id", project_id)
        payload = write["payload"]
        assert payload["merge_state"] == MergeState.FINALIZED.value
        # Frozen config is a JSONB-safe dict — spot-check a few fields
        # we know are scalar.
        frozen = payload["enrichment_config_frozen"]
        assert isinstance(frozen, dict)
        assert "recent_window_days" in frozen
        assert frozen["recent_window_days"] == 336  # DEFAULT_CONFIG value


class TestFinalizeIdempotency:
    """A second /finalize on the same project must 409."""

    def test_returns_409_on_already_finalized(
        self, patched_server, project_id, pre_merge_graph
    ):
        first = patched_server.post(f"/projects/{project_id}/finalize")
        assert first.status_code == 200
        assert pre_merge_graph.merge_state == MergeState.FINALIZED

        second = patched_server.post(f"/projects/{project_id}/finalize")
        assert second.status_code == 409
        assert second.json() == {"error": "project_finalized"}


class TestFinalizeErrorPaths:
    """Sad-path coverage: missing graph + unexpected state."""

    def test_returns_404_when_graph_not_loaded(self, patched_server, project_id):
        from src.graph_store import graph_store
        graph_store.delete(project_id)
        response = patched_server.post(f"/projects/{project_id}/finalize")
        assert response.status_code == 404
        assert "not loaded" in response.json()["error"].lower()


# ---------------------------------------------------------------------------
# Round-trip through /load — confirms §M persistence contract
# ---------------------------------------------------------------------------
class _RoundTripExec:
    def __init__(self, recorder: list, payload, eq_filter: tuple) -> None:
        self._recorder = recorder
        self._payload = payload
        self._eq_filter = eq_filter

    def execute(self):
        self._recorder.append({
            "filter": self._eq_filter,
            "payload": dict(self._payload) if self._payload is not None else None,
        })
        class _Resp:
            data: list = []
        return _Resp()


class _RoundTripUpdate:
    def __init__(self, recorder: list, payload: dict) -> None:
        self._recorder = recorder
        self._payload = payload

    def eq(self, col: str, val):
        return _RoundTripExec(self._recorder, self._payload, (col, val))


class _RoundTripSelect:
    """Mimics ``.select(...).eq(col, val).single().execute()`` returning a
    row dict the load path expects.
    """

    def __init__(self, recorder: list, row: dict) -> None:
        self._recorder = recorder
        self._row = row
        self._eq_filter: tuple = ()

    def eq(self, col: str, val):
        self._eq_filter = (col, val)
        return self

    def single(self):
        return self

    def execute(self):
        self._recorder.append({"filter": self._eq_filter, "select": True})

        class _Resp:
            pass

        r = _Resp()
        r.data = self._row
        return r


class _RoundTripTable:
    def __init__(self, recorder: list, row_store: dict, name: str) -> None:
        self._recorder = recorder
        self._row_store = row_store
        self._name = name

    def update(self, payload: dict) -> _RoundTripUpdate:
        # Mirror the update onto the shared row store so a subsequent
        # select picks the new ``merge_state`` up.
        for k, v in payload.items():
            self._row_store[k] = v
        return _RoundTripUpdate(self._recorder, payload)

    def select(self, _cols: str) -> _RoundTripSelect:
        return _RoundTripSelect(self._recorder, dict(self._row_store))


class _RoundTripSupabaseClient:
    """Stub that round-trips ``merge_state`` between /finalize's update
    and /load's select against an in-process row dict.
    """

    def __init__(self, project_id: str, name: str = "test-finalize") -> None:
        self.writes: list = []
        self.row: dict = {
            "id": project_id,
            "name": name,
            "status": "ready",
            "merge_state": MergeState.PRE_MERGE.value,
        }

    def table(self, name: str) -> _RoundTripTable:
        return _RoundTripTable(self.writes, self.row, name)


class TestFinalizeRoundTripThroughLoad:
    """End-to-end §M check: /finalize writes ``merge_state=FINALIZED`` to
    Supabase, then /load reads it back onto a fresh in-memory Graph.
    """

    def test_finalize_then_load_restores_finalized_state(
        self, monkeypatch, pre_merge_graph, project_id, tmp_path
    ):
        from fastapi.testclient import TestClient
        from src import server
        from src import processor as v2_processor
        from src.graph_store import graph_store
        from src.smart_merge.state_store import smart_merge_state_store

        # Shared stub Supabase client — patches BOTH the
        # ``get_service_client`` factory (used by /finalize) AND the
        # direct ``create_client`` call (used by /load).
        stub = _RoundTripSupabaseClient(project_id)
        monkeypatch.setattr(server, "get_service_client", lambda: stub)
        monkeypatch.setattr(server, "create_client", lambda *_a, **_kw: stub)

        # Redirect pickle dumps + reads to a tmp dir so /finalize's
        # ``save_graph_to_disk`` and /load's ``load_graph_v2_from_disk``
        # share the same on-disk location.
        pickle_dir = tmp_path / "pickles"
        pickle_dir.mkdir()
        monkeypatch.setattr(
            v2_processor,
            "_project_pickle_dir",
            lambda pid: pickle_dir / pid,
        )

        graph_store.set(project_id, pre_merge_graph)
        smart_merge_state_store.reset(project_id)
        server.current_project_id = project_id
        server.current_project_name = "test-finalize"

        try:
            client = TestClient(server.app)

            # ----- 1. Finalize -----
            fin_resp = client.post(f"/projects/{project_id}/finalize")
            assert fin_resp.status_code == 200, fin_resp.text
            assert fin_resp.json()["merge_state"] == MergeState.FINALIZED.value

            # Supabase row reflects FINALIZED + carries a frozen config.
            assert stub.row["merge_state"] == MergeState.FINALIZED.value
            assert isinstance(stub.row.get("enrichment_config_frozen"), dict)

            # The on-disk pickle directory was re-dumped.
            assert (pickle_dir / project_id).exists()

            # ----- 2. Drop the in-memory graph + load fresh -----
            graph_store.delete(project_id)
            smart_merge_state_store.delete(project_id)

            load_resp = client.post(f"/projects/{project_id}/load")
            assert load_resp.status_code == 200, load_resp.text

            # The freshly loaded Graph carries FINALIZED state from
            # Supabase (the row is the source of truth per §M).
            reloaded = graph_store.get(project_id)
            assert reloaded is not None
            assert reloaded.merge_state == MergeState.FINALIZED

            # UU aftermath §Bug 2: ``/projects/current`` reports the
            # rebind-populated UU count, not the stale smart-merge
            # ``state.users`` length. Post-finalize the rebind seeds a
            # singleton UU per orphan Account, so the count matches
            # ``len(graph.unified_users.all())`` exactly.
            current_resp = client.get("/projects/current")
            assert current_resp.status_code == 200, current_resp.text
            assert current_resp.json()["stats"]["unified_users"] == len(
                reloaded.unified_users.all()
            )
        finally:
            graph_store.delete(project_id)
            smart_merge_state_store.delete(project_id)
            server.current_project_id = None
            server.current_project_name = None


# ---------------------------------------------------------------------------
# UU aftermath §Bug 3 — defensive cleanup pass drops leftover
# Account-keyed relations.
# ---------------------------------------------------------------------------
class TestFinalizeCleanupDropsAccountKeyedRelations:
    """The /finalize handler's defensive cleanup pass — runs before rebind,
    drops every relation row whose source/target carries an account-kind
    ref (``GIT_ACCOUNT`` / ``GITHUB_USER`` / ``JIRA_USER``).

    The leak path it guards against: Phase A historically emitting a
    people-side relation with an Account-kind endpoint (a hypothetical
    builder misclassification, or a stale on-disk pickle from before
    the UU redesign landed). Post-rebind those rows would coexist with
    Phase B's correctly-keyed UNIFIED_USER rows, double-counting any
    consumer that walks ``graph.relations.of_kind(...)``.
    """

    def test_pre_seeded_account_keyed_row_is_gone_after_finalize(
        self, patched_server, project_id, pre_merge_graph
    ):
        # Pre-seed a leftover Phase A leak: an ``ownership`` row keyed
        # on a ``GIT_ACCOUNT`` source (Alice). After finalize the
        # cleanup pass must drop this row before rebind runs.
        from src.enrichment.relations import Relation, WindowKind
        from src.common.domains.git.models import File

        # Reuse the git project the fixture already added so the new
        # file's ``project_ref`` resolves.
        git_proj = next(
            p for p in pre_merge_graph.git_projects.all()
            if p.name == "demo-git"
        )
        file_ = File(
            id="src/leftover.py",
            path="src/leftover.py",
            project_ref=git_proj.ref(),
            extension="py",
        )
        pre_merge_graph.files.add(file_)

        # Reach into ``graph.git_accounts`` for Alice's ref — the
        # fixture builder above adds her with the standard make_id.
        alice = next(
            a for a in pre_merge_graph.git_accounts.all()
            if a.name == ALICE_NAME
        )
        alice_account_ref = alice.ref()
        assert alice_account_ref.kind == EntityKind.GIT_ACCOUNT

        leftover_id = Relation.canonical_id(
            alice_account_ref,
            file_.ref(),
            "ownership",
            WindowKind.LIFETIME,
        )
        leftover = Relation(
            id=leftover_id,
            source=alice_account_ref,
            target=file_.ref(),
            relation_kind="ownership",
            window=WindowKind.LIFETIME,
            strength=0.5,
            extras={},
        )
        pre_merge_graph.relations.add(leftover)
        assert pre_merge_graph.relations.get(leftover_id) is not None

        # Finalize.
        resp = patched_server.post(f"/projects/{project_id}/finalize")
        assert resp.status_code == 200, resp.text

        # The leftover row is gone — the cleanup pass dropped it before
        # rebind ran, so it never reached Phase B's emission stage.
        assert pre_merge_graph.relations.get(leftover_id) is None

        # And: no relation row anywhere in the registry carries an
        # account-kind endpoint after finalize.
        account_kinds = {
            EntityKind.GIT_ACCOUNT,
            EntityKind.GITHUB_USER,
            EntityKind.JIRA_USER,
        }
        for rel in pre_merge_graph.relations.all():
            assert rel.source.kind not in account_kinds, rel
            assert rel.target.kind not in account_kinds, rel
