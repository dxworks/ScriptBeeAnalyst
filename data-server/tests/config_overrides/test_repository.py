"""Unit tests for :class:`ConfigOverridesRepository`.

The repository is exercised against the Supabase fake from
``conftest.py`` — no real network. Each test installs its own store
shape and asserts on either the returned model or the recorded calls.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.config_overrides.conftest import patched_supabase  # noqa: F401

from src.config_overrides.repository import ConfigOverridesRepository


class TestGet:
    def test_returns_empty_row_when_project_has_no_overrides(self):
        with patched_supabase(store={"project_config_overrides": []}):
            row = ConfigOverridesRepository().get("missing-project")
        assert row.project_id == "missing-project"
        assert row.overrides == {}
        assert row.updated_at is None

    def test_returns_persisted_overrides(self):
        store = {
            "project_config_overrides": [
                {
                    "project_id": "p1",
                    "overrides": {"bugmagnet_min_bugfix_commits": 8},
                    "updated_at": "2026-05-24T10:00:00+00:00",
                }
            ]
        }
        with patched_supabase(store=store):
            row = ConfigOverridesRepository().get("p1")
        assert row.overrides == {"bugmagnet_min_bugfix_commits": 8}
        assert row.updated_at is not None
        assert row.updated_at.isoformat() == "2026-05-24T10:00:00+00:00"

    def test_degrades_to_empty_on_supabase_exception(self):
        """The build path must stay alive when Supabase is unreachable."""
        def _boom():
            raise RuntimeError("supabase unreachable")

        with patch("src.config_overrides.repository.get_service_client", _boom):
            row = ConfigOverridesRepository().get("p1")
        assert row.overrides == {}
        assert row.updated_at is None

    def test_non_dict_overrides_value_is_normalised_to_empty(self):
        """Corrupt JSONB shape (overrides not a dict) → safe fallback."""
        store = {
            "project_config_overrides": [
                {"project_id": "p1", "overrides": "not-a-dict", "updated_at": None}
            ]
        }
        with patched_supabase(store=store):
            row = ConfigOverridesRepository().get("p1")
        assert row.overrides == {}


class TestUpsert:
    def test_inserts_new_row(self):
        store = {"project_config_overrides": []}
        with patched_supabase(store=store) as (s, recorder):
            row = ConfigOverridesRepository().upsert(
                "p1", {"bugmagnet_min_bugfix_commits": 9}
            )
        assert row.project_id == "p1"
        assert row.overrides == {"bugmagnet_min_bugfix_commits": 9}
        # Side effect: row landed in the store.
        assert len(s["project_config_overrides"]) == 1
        assert s["project_config_overrides"][0]["project_id"] == "p1"
        # Action was an upsert (not an insert).
        actions = [c["action"] for c in recorder["calls"]]
        assert "upsert" in actions

    def test_replaces_existing_row(self):
        store = {
            "project_config_overrides": [
                {
                    "project_id": "p1",
                    "overrides": {"bugmagnet_min_bugfix_commits": 5},
                    "updated_at": "2026-05-23T10:00:00+00:00",
                }
            ]
        }
        with patched_supabase(store=store) as (s, _):
            row = ConfigOverridesRepository().upsert(
                "p1", {"bugmagnet_min_bugfix_commits": 9, "pulsar_cv_min": 0.7}
            )
        assert row.overrides == {
            "bugmagnet_min_bugfix_commits": 9,
            "pulsar_cv_min": 0.7,
        }
        # Still exactly one row for p1.
        assert len(s["project_config_overrides"]) == 1


class TestDelete:
    def test_deletes_existing_row(self):
        store = {
            "project_config_overrides": [
                {"project_id": "p1", "overrides": {}, "updated_at": None}
            ]
        }
        with patched_supabase(store=store) as (s, _):
            removed = ConfigOverridesRepository().delete("p1")
        assert removed is True
        assert s["project_config_overrides"] == []

    def test_delete_is_idempotent(self):
        """Deleting a non-existent row returns False but does not raise."""
        with patched_supabase(store={"project_config_overrides": []}) as (s, _):
            removed = ConfigOverridesRepository().delete("missing")
        assert removed is False
