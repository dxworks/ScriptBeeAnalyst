"""Router-level integration tests for ``/projects/{id}/config-overrides``.

Drives the live FastAPI app via :class:`TestClient` with Supabase fully
mocked. Covers happy paths, 422 validation rejections (unknown / hidden /
shape mismatch), 404 on unknown project, DELETE idempotence, and one
end-to-end build-graph integration that proves the PUT-stored override
reaches the merged :class:`EnrichmentConfig`.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from tests.config_overrides.conftest import patched_supabase  # noqa: F401

from fastapi.testclient import TestClient

from src import processor as v2_processor
from src import server


PROJECT_ID = "p1"


def _store_with_project(extra: Dict[str, List[Dict[str, Any]]] | None = None):
    store: Dict[str, List[Dict[str, Any]]] = {
        "projects": [{"id": PROJECT_ID, "name": "Demo"}],
        "project_config_overrides": [],
    }
    if extra:
        for table, rows in extra.items():
            store[table] = rows
    return store


class TestGet:
    def test_returns_catalogue_and_empty_overrides(self):
        with patched_supabase(store=_store_with_project()):
            client = TestClient(server.app)
            response = client.get(f"/projects/{PROJECT_ID}/config-overrides")
        assert response.status_code == 200, response.text
        body = response.json()
        assert "catalogue" in body
        assert "families" in body["catalogue"]
        assert body["overrides"] == {}
        assert body["updated_at"] is None

    def test_catalogue_current_reflects_stored_overrides(self):
        store = _store_with_project({
            "project_config_overrides": [
                {
                    "project_id": PROJECT_ID,
                    "overrides": {"bugmagnet_min_bugfix_commits": 12},
                    "updated_at": "2026-05-24T10:00:00+00:00",
                }
            ]
        })
        with patched_supabase(store=store):
            client = TestClient(server.app)
            response = client.get(f"/projects/{PROJECT_ID}/config-overrides")
        assert response.status_code == 200
        body = response.json()
        assert body["overrides"] == {"bugmagnet_min_bugfix_commits": 12}

        # The catalogue's CURRENT for that field equals the override; the
        # DEFAULT remains the dataclass default (5).
        fields_flat = [
            f for fam in body["catalogue"]["families"] for f in fam["fields"]
        ]
        bug = next(f for f in fields_flat if f["name"] == "bugmagnet_min_bugfix_commits")
        assert bug["current"] == 12
        assert bug["default"] == 5

    def test_unknown_project_returns_404(self):
        # No project row in the store.
        store: Dict[str, List[Dict[str, Any]]] = {
            "projects": [],
            "project_config_overrides": [],
        }
        with patched_supabase(store=store):
            client = TestClient(server.app)
            response = client.get(f"/projects/{PROJECT_ID}/config-overrides")
        assert response.status_code == 404


class TestPut:
    def test_happy_path_writes_and_round_trips(self):
        with patched_supabase(store=_store_with_project()) as (s, _):
            client = TestClient(server.app)
            put_response = client.put(
                f"/projects/{PROJECT_ID}/config-overrides",
                json={"overrides": {"bugmagnet_min_bugfix_commits": 9}},
            )
            assert put_response.status_code == 200, put_response.text
            assert put_response.json()["overrides"] == {"bugmagnet_min_bugfix_commits": 9}

            get_response = client.get(f"/projects/{PROJECT_ID}/config-overrides")
            assert get_response.json()["overrides"] == {"bugmagnet_min_bugfix_commits": 9}

    def test_normalises_dict_shape_for_issue_age_buckets(self):
        """UI may send dict shape; storage holds compact ``[label, value]`` form."""
        with patched_supabase(store=_store_with_project()) as (s, _):
            client = TestClient(server.app)
            response = client.put(
                f"/projects/{PROJECT_ID}/config-overrides",
                json={"overrides": {
                    "issue_age_buckets": [
                        {"label": "<1w", "max_days": 7},
                        {"label": ">1w", "max_days": 1000000000},
                    ]
                }},
            )
            assert response.status_code == 200, response.text
            # Storage holds compact form, not dict form.
            stored = s["project_config_overrides"][0]["overrides"]
            assert stored == {"issue_age_buckets": [["<1w", 7], [">1w", 1000000000]]}

    def test_normalises_dict_shape_for_nature_patterns(self):
        with patched_supabase(store=_store_with_project()) as (s, _):
            client = TestClient(server.app)
            response = client.put(
                f"/projects/{PROJECT_ID}/config-overrides",
                json={"overrides": {
                    "nature_patterns": [
                        {"label": "hotfix", "regex": r"^HOTFIX:"},
                    ]
                }},
            )
            assert response.status_code == 200, response.text
            stored = s["project_config_overrides"][0]["overrides"]
            assert stored == {"nature_patterns": [["hotfix", r"^HOTFIX:"]]}

    def test_empty_overrides_put_clears_prior_state(self):
        """PUT {} replaces the row with an empty dict and bumps updated_at.

        Proves the "clear all overrides" UX path: a Discard-and-Save with
        no remaining edits empties the row but still records the action.
        """
        with patched_supabase(store=_store_with_project()):
            client = TestClient(server.app)

            # Seed with one knob.
            first = client.put(
                f"/projects/{PROJECT_ID}/config-overrides",
                json={"overrides": {"bugmagnet_min_bugfix_commits": 9}},
            )
            assert first.status_code == 200, first.text
            first_updated = first.json()["updated_at"]
            assert first_updated is not None

            # Confirm it landed.
            mid = client.get(f"/projects/{PROJECT_ID}/config-overrides")
            assert mid.json()["overrides"] == {"bugmagnet_min_bugfix_commits": 9}

            # Clear with an empty payload.
            second = client.put(
                f"/projects/{PROJECT_ID}/config-overrides",
                json={"overrides": {}},
            )
            assert second.status_code == 200, second.text
            body = second.json()
            assert body["overrides"] == {}
            assert body["updated_at"] is not None
            assert body["updated_at"] > first_updated, (
                "updated_at must advance on the clearing write "
                f"(first={first_updated}, second={body['updated_at']})"
            )

            # Final GET confirms the row is empty.
            after = client.get(f"/projects/{PROJECT_ID}/config-overrides")
            assert after.status_code == 200
            assert after.json()["overrides"] == {}

    def test_catalogue_current_round_trip_after_dict_shape_put(self):
        """End-to-end normalise → store → catalogue overlay for a composite field.

        PUT issue_age_buckets in DICT shape, GET, assert:

        * the catalogue field's ``current`` reflects the value,
        * the stored row holds the COMPACT shape.

        This is the proof that the dict→compact normalisation feeds back
        through the catalogue's ``current`` overlay without the UI having
        to reshape on its side.
        """
        with patched_supabase(store=_store_with_project()) as (s, _):
            client = TestClient(server.app)

            put_response = client.put(
                f"/projects/{PROJECT_ID}/config-overrides",
                json={"overrides": {
                    "issue_age_buckets": [
                        {"label": "<3d", "max_days": 3},
                        {"label": ">3d", "max_days": 1000000000},
                    ]
                }},
            )
            assert put_response.status_code == 200, put_response.text

            # Storage: compact form.
            stored = s["project_config_overrides"][0]["overrides"]
            assert stored == {
                "issue_age_buckets": [["<3d", 3], [">3d", 1000000000]],
            }

            # Catalogue overlay: `current` reflects the stored value.
            get_response = client.get(f"/projects/{PROJECT_ID}/config-overrides")
            assert get_response.status_code == 200
            body = get_response.json()
            assert body["overrides"] == {
                "issue_age_buckets": [["<3d", 3], [">3d", 1000000000]],
            }

            fields_flat = [
                f for fam in body["catalogue"]["families"] for f in fam["fields"]
            ]
            iab = next(f for f in fields_flat if f["name"] == "issue_age_buckets")
            assert iab["current"] == [["<3d", 3], [">3d", 1000000000]]
            # Default still surfaces unchanged for comparison.
            assert iab["default"] == [
                ["<1w", 7], ["1-4w", 28], ["1-3m", 90],
                ["3-12m", 365], [">1y", 1000000000],
            ]

    def test_unknown_field_returns_422(self):
        with patched_supabase(store=_store_with_project()):
            client = TestClient(server.app)
            response = client.put(
                f"/projects/{PROJECT_ID}/config-overrides",
                json={"overrides": {"nonexistent_knob": 1}},
            )
        assert response.status_code == 422
        body = response.json()
        assert body["field"] == "nonexistent_knob"
        assert body["error"] == "unknown"

    def test_hidden_field_returns_422(self):
        with patched_supabase(store=_store_with_project()):
            client = TestClient(server.app)
            response = client.put(
                f"/projects/{PROJECT_ID}/config-overrides",
                json={"overrides": {"components_mapping_data": {"core": {}}}},
            )
        assert response.status_code == 422
        body = response.json()
        assert body["field"] == "components_mapping_data"
        assert body["error"] == "not editable"

    def test_bad_shape_returns_422_with_field_name(self):
        with patched_supabase(store=_store_with_project()):
            client = TestClient(server.app)
            response = client.put(
                f"/projects/{PROJECT_ID}/config-overrides",
                json={"overrides": {"bugmagnet_min_bugfix_commits": "nine"}},
            )
        assert response.status_code == 422
        body = response.json()
        assert body["field"] == "bugmagnet_min_bugfix_commits"
        # Error message names the type and the value for debuggability.
        assert "str" in body["error"]
        assert "'nine'" in body["error"]

    def test_bad_regex_returns_422(self):
        with patched_supabase(store=_store_with_project()):
            client = TestClient(server.app)
            response = client.put(
                f"/projects/{PROJECT_ID}/config-overrides",
                json={"overrides": {"test_patterns": ["[unclosed"]}},
            )
        assert response.status_code == 422
        body = response.json()
        assert body["field"] == "test_patterns"

    def test_unknown_project_returns_404(self):
        store: Dict[str, List[Dict[str, Any]]] = {
            "projects": [],
            "project_config_overrides": [],
        }
        with patched_supabase(store=store):
            client = TestClient(server.app)
            response = client.put(
                f"/projects/{PROJECT_ID}/config-overrides",
                json={"overrides": {"bugmagnet_min_bugfix_commits": 9}},
            )
        assert response.status_code == 404


class TestDelete:
    def test_delete_clears_row(self):
        store = _store_with_project({
            "project_config_overrides": [
                {"project_id": PROJECT_ID, "overrides": {"x": 1}, "updated_at": None}
            ]
        })
        with patched_supabase(store=store) as (s, _):
            client = TestClient(server.app)
            response = client.delete(f"/projects/{PROJECT_ID}/config-overrides")
        assert response.status_code == 204
        assert s["project_config_overrides"] == []

    def test_delete_is_idempotent(self):
        with patched_supabase(store=_store_with_project()):
            client = TestClient(server.app)
            response = client.delete(f"/projects/{PROJECT_ID}/config-overrides")
        assert response.status_code == 204


# ---------------------------------------------------------------------------
# End-to-end: PUT → build_graph → merged EnrichmentConfig reflects override.
# ---------------------------------------------------------------------------
class TestEndToEndBuildGraph:
    def test_put_override_flows_into_build_graph(self):
        """The single test that proves the whole chain works.

        1. PUT an override via the router.
        2. Invoke ``processor.build_graph`` with the same patched Supabase.
        3. Assert ``build_graph_from_bundles`` received an
           :class:`EnrichmentConfig` whose field equals the override.
        """
        seen: Dict[str, Any] = {"config": None}

        def _fake_download(_pid):
            return v2_processor.DownloadedFiles()

        def _fake_bundles(_d, project_name="X"):
            return {}

        def _fake_build_from_bundles(project_id, bundles, *, config=None):
            seen["config"] = config
            from src.common.kernel import Graph
            from src.enrichment.pipeline import PipelineResult
            return Graph(project_id=project_id), PipelineResult(
                builders_run=[], metrics_run=[], errors=[]
            )

        def _fake_save(_g):
            from pathlib import Path
            return Path("/tmp/noop")

        with patched_supabase(store=_store_with_project()):
            # Step 1: persist an override via the API.
            client = TestClient(server.app)
            put_response = client.put(
                f"/projects/{PROJECT_ID}/config-overrides",
                json={"overrides": {
                    "bugmagnet_min_bugfix_commits": 9,
                    "pulsar_cv_min": 0.7,
                }},
            )
            assert put_response.status_code == 200, put_response.text

            # Step 2: run the build path with the same Supabase fake.
            with patch.object(v2_processor, "download_serialized_files_from_supabase", _fake_download), \
                 patch.object(v2_processor, "_downloaded_files_to_bundles", _fake_bundles), \
                 patch.object(v2_processor, "build_graph_from_bundles", _fake_build_from_bundles), \
                 patch.object(v2_processor, "save_graph_to_disk", _fake_save), \
                 patch.object(v2_processor, "fetch_project_component_mapping", return_value=None):
                v2_processor.build_graph(PROJECT_ID, project_name="Demo")

        # Step 3: the merged config the pipeline would have seen.
        cfg = seen["config"]
        assert cfg is not None
        assert cfg.bugmagnet_min_bugfix_commits == 9
        assert cfg.pulsar_cv_min == 0.7
