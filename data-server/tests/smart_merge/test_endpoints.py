"""End-to-end integration tests for the migrated smart-merge endpoints.

Chunk 19 — Phase 2 of the graph-v2 refactor.

These tests exercise each of the rewritten endpoints in ``src/server.py``
via :class:`fastapi.testclient.TestClient`, with the typed v2
:class:`Graph` injected into ``graph_store`` and an in-memory
:class:`SmartMergeRepository` standing in for the Supabase-backed real
thing (see :mod:`tests.smart_merge.conftest`).

Endpoints covered (one test class per endpoint):

* ``GET  /projects/{id}/authors/suggestions`` — `TestSuggestionsEndpoint`
* ``POST /projects/{id}/authors/suggestions/apply`` — `TestApplySuggestion`
* ``GET  /projects/{id}/authors/users`` + replay flow — `TestUsersAndReplay`
* ``DELETE /projects/{id}/authors/users`` (wipe-all) — `TestDeleteAllUsers`
* ``POST /projects/{id}/save-graph-state`` (post-migration smoke) — `TestSaveGraphState`

Each test asserts both the HTTP response shape AND the side-effect on
the in-memory repo (the persistence layer the real endpoint writes
into).
"""
from __future__ import annotations


class TestSuggestionsEndpoint:
    """``GET /projects/{id}/authors/suggestions``

    Derives ``SourceIdentity`` from the typed Graph (git_accounts +
    jira_users + github_users), runs the engine, returns suggestions.
    """

    def test_returns_a_high_confidence_cluster_for_alice(self, patched_server, project_id):
        from tests.smart_merge.conftest import ALICE_NAME

        response = patched_server.get(f"/projects/{project_id}/authors/suggestions")
        assert response.status_code == 200, response.text
        body = response.json()

        # Three "Alice" identities + one Bob = 4 total.
        assert body["total_identities"] == 4
        assert body["existing_users"] == 0
        assert len(body["suggestions"]) >= 1

        # The Alice cluster should pull in all 3 of her identities.
        alice = max(body["suggestions"], key=lambda s: len(s["identities"]))
        assert len(alice["identities"]) == 3
        sources = sorted(i["source"] for i in alice["identities"])
        assert sources == ["git", "github", "jira"]
        assert alice["default_name"] == ALICE_NAME

    def test_returns_400_when_no_graph_loaded(self, patched_server, project_id):
        from src.graph_store import graph_store
        graph_store.delete(project_id)
        response = patched_server.get(f"/projects/{project_id}/authors/suggestions")
        assert response.status_code == 400
        assert "not loaded" in response.json()["error"].lower()


class TestApplySuggestion:
    """``POST /projects/{id}/authors/suggestions/apply``

    Creates a :class:`UnifiedUser` from selected identities, persists it
    via the repo, mutates the in-memory state.
    """

    def test_apply_creates_user_and_persists_mapping(
        self, patched_server, project_id, in_memory_repo
    ):
        from tests.smart_merge.conftest import ALICE_EMAIL, ALICE_NAME

        # Pre-condition: fetch suggestions so the cache is populated and
        # we know the Alice cluster's identity keys.
        sugg_resp = patched_server.get(f"/projects/{project_id}/authors/suggestions")
        assert sugg_resp.status_code == 200
        body = sugg_resp.json()
        alice = max(body["suggestions"], key=lambda s: len(s["identities"]))
        selected_keys = [
            f"{i['source']}:{i['source_key']}" for i in alice["identities"]
        ]

        # Apply.
        apply_resp = patched_server.post(
            f"/projects/{project_id}/authors/suggestions/apply",
            json={
                "suggestion_id": alice["suggestion_id"],
                "selected_identity_keys": selected_keys,
                "unselected_identity_keys": [],
                "name": ALICE_NAME,
                "email": ALICE_EMAIL,
            },
        )
        assert apply_resp.status_code == 200, apply_resp.text
        created = apply_resp.json()
        assert created["display_name"] == ALICE_NAME
        assert created["primary_email"] == ALICE_EMAIL
        assert len(created["identities"]) == 3

        # Repo side-effect: exactly one mapping persisted.
        mappings = in_memory_repo.get_user_mappings(project_id)
        assert len(mappings) == 1
        assert mappings[0].display_name == ALICE_NAME
        assert len(mappings[0].identities) == 3

    def test_apply_rejects_when_fewer_than_two_identities(
        self, patched_server, project_id
    ):
        from tests.smart_merge.conftest import ALICE_EMAIL, ALICE_NAME

        # Force the cache to load.
        patched_server.get(f"/projects/{project_id}/authors/suggestions")

        apply_resp = patched_server.post(
            f"/projects/{project_id}/authors/suggestions/apply",
            json={
                "suggestion_id": "any-id",
                "selected_identity_keys": [f"git:{ALICE_NAME} <{ALICE_EMAIL}>"],
                "unselected_identity_keys": [],
                "name": "Solo",
                "email": "solo@example.com",
            },
        )
        assert apply_resp.status_code == 400
        assert "at least 2" in apply_resp.json()["error"].lower()


class TestUsersAndReplay:
    """``GET /projects/{id}/authors/users`` + ``POST /authors/users/replay``

    Asserts the replay path round-trips: persist mapping → replay → users
    surface in the GET response with live activity stats.
    """

    def test_users_endpoint_starts_empty(self, patched_server, project_id):
        resp = patched_server.get(f"/projects/{project_id}/authors/users")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["users"] == []

    def test_replay_then_get_users_reflects_persisted_mappings(
        self, patched_server, project_id, in_memory_repo, three_source_graph
    ):
        from tests.smart_merge.conftest import ALICE_EMAIL, ALICE_LOGIN, ALICE_NAME
        from src.smart_merge.identity import SourceIdentity
        from src.smart_merge.types import UserMapping

        # Pre-seed a persisted mapping (as if smart-merge had run previously).
        # source_key shapes match the typed Graph fixture so replay
        # correlates against extract_all_identities().
        identities = [
            SourceIdentity(
                source="git",
                name=ALICE_NAME,
                email=ALICE_EMAIL,
                login=None,
                source_key=f"{ALICE_NAME} <{ALICE_EMAIL}>",
            ),
            SourceIdentity(
                source="github",
                name=ALICE_NAME,
                email=None,
                login=ALICE_LOGIN,
                source_key=f"https://github.com/{ALICE_LOGIN}",
            ),
        ]
        in_memory_repo.upsert_user_mapping(
            UserMapping(
                unified_user_id="uuid-alice",
                display_name=ALICE_NAME,
                primary_email=ALICE_EMAIL,
                identities=identities,
            ),
            project_id,
        )

        # Trigger replay (this is what /load does automatically).
        replay_resp = patched_server.post(
            f"/projects/{project_id}/authors/users/replay"
        )
        assert replay_resp.status_code == 200
        assert replay_resp.json()["users_replayed"] == 1
        assert replay_resp.json()["identities_matched"] == 2

        # GET surfaces the replayed user.
        users_resp = patched_server.get(f"/projects/{project_id}/authors/users")
        assert users_resp.status_code == 200
        users_body = users_resp.json()
        assert users_body["total"] == 1
        only = users_body["users"][0]
        assert only["id"] == "uuid-alice"
        assert only["display_name"] == ALICE_NAME
        # Bound to typed graph → stats accessors resolve cleanly even
        # though the graph has zero commits/issues/PRs.
        assert only["stats"] == {
            "commit_count": 0,
            "issue_count": 0,
            "pr_count": 0,
        }


class TestDeleteAllUsers:
    """``DELETE /projects/{id}/authors/users``

    The Phase-1 implementation called the dormant
    ``_persist_project_pickle`` which crashed with
    ``ImportError: upload_pickle_to_supabase``. Chunk 19 removes the
    pickle path: this endpoint now just wipes the Supabase rows + the
    in-memory state and returns ok.
    """

    def test_delete_all_clears_persisted_state_and_returns_counts(
        self, patched_server, project_id, in_memory_repo
    ):
        # Pre-seed: 1 mapping + 1 rejected pair.
        from src.smart_merge.identity import SourceIdentity
        from src.smart_merge.types import RejectedPair, UserMapping

        in_memory_repo.upsert_user_mapping(
            UserMapping(
                unified_user_id="uuid-x",
                display_name="X",
                primary_email=None,
                identities=[SourceIdentity(
                    source="git", name="X", email=None, login=None,
                    source_key="X <x@x>",
                )],
            ),
            project_id,
        )
        in_memory_repo.add_rejected_similarities(project_id, [RejectedPair(
            project_id=project_id,
            first_source="git", first_source_key="a",
            second_source="git", second_source_key="b",
        )])

        # Hit the migrated endpoint.
        resp = patched_server.delete(f"/projects/{project_id}/authors/users")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["deleted_users"] == 1
        assert body["deleted_rejected"] == 1

        # Both tables are empty post-call.
        assert in_memory_repo.get_user_mappings(project_id) == []
        assert in_memory_repo.get_rejected_similarities(project_id) == []


class TestSaveGraphState:
    """``POST /projects/{id}/save-graph-state``

    Smoke test for the post-migration shape. Previously called the
    crashy ``_persist_project_pickle``; now a no-op returning 200 so the
    web UI's "save merge state" button continues to work without
    triggering the dead pickle path.
    """

    def test_returns_ok_with_zero_size_and_user_count(
        self, patched_server, project_id
    ):
        resp = patched_server.post(f"/projects/{project_id}/save-graph-state")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        # No upload happens in v2.
        assert body["size_mb"] == 0.0
        # Fresh state has no users.
        assert body["user_count"] == 0

    def test_returns_400_when_graph_not_loaded(self, patched_server, project_id):
        from src.graph_store import graph_store
        graph_store.delete(project_id)
        resp = patched_server.post(f"/projects/{project_id}/save-graph-state")
        assert resp.status_code == 400
