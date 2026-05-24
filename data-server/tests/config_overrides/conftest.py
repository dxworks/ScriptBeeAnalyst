"""Shared test scaffolding for the config-overrides feature.

Provides a minimal Supabase-fake builder that mirrors the
``postgrest`` request-builder chain the production code uses:
``client.table(name).select(...).eq(...).limit(...).execute()`` and the
upsert / delete variants. Tests configure the fake per-scenario; the
:func:`patched_supabase` helper installs it on the import sites the
code reaches for.

This pattern matches :mod:`tests.chunk_08.test_components_endpoints`
and :mod:`tests.chunk_08.test_processor_component_mapping_fetch` —
no real network calls, no Supabase test container.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from unittest.mock import patch

# Env shims so importing the server module doesn't blow up. Mirrors the
# pattern used by the smart_merge / chunk_08 fixtures.
os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake")
os.environ.setdefault("WORKSPACE_ROOT", "/tmp")


class FakeResponse:
    def __init__(self, data: List[Dict[str, Any]]):
        self.data = data


class FakeQuery:
    """Records each call so a test can assert on the write payload."""

    def __init__(self, table_name: str, store: Dict[str, Any], recorder: Dict[str, Any]):
        self._table = table_name
        self._store = store
        self._recorder = recorder
        self._eq_filters: List[tuple[str, Any]] = []
        self._upsert_payload: Optional[Dict[str, Any]] = None
        self._action: Optional[str] = None

    # Chain methods — each returns ``self`` so the builder pattern works.
    def select(self, *_args, **_kwargs):
        if self._action is None:
            self._action = "select"
        return self

    def eq(self, field: str, value: Any):
        self._eq_filters.append((field, value))
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def upsert(self, payload: Dict[str, Any], **_kwargs):
        self._action = "upsert"
        self._upsert_payload = payload
        return self

    def insert(self, payload: Dict[str, Any], **_kwargs):
        self._action = "insert"
        self._upsert_payload = payload
        return self

    def delete(self):
        self._action = "delete"
        return self

    def execute(self):
        self._recorder.setdefault("calls", []).append({
            "table": self._table,
            "action": self._action,
            "eq": list(self._eq_filters),
            "payload": self._upsert_payload,
        })

        if self._action == "select":
            rows = self._store.get(self._table, [])
            for field, value in self._eq_filters:
                rows = [r for r in rows if r.get(field) == value]
            return FakeResponse(rows)

        if self._action == "upsert":
            assert self._upsert_payload is not None
            self._store.setdefault(self._table, [])
            # Replace by primary key if present, else append.
            pk = self._upsert_payload.get("project_id")
            existing = self._store[self._table]
            for i, row in enumerate(existing):
                if row.get("project_id") == pk:
                    existing[i] = {**row, **self._upsert_payload}
                    return FakeResponse([existing[i]])
            existing.append({**self._upsert_payload, "updated_at": "2026-05-24T12:00:00+00:00"})
            return FakeResponse([existing[-1]])

        if self._action == "delete":
            rows = self._store.get(self._table, [])
            removed = []
            kept = []
            for row in rows:
                if all(row.get(f) == v for f, v in self._eq_filters):
                    removed.append(row)
                else:
                    kept.append(row)
            self._store[self._table] = kept
            return FakeResponse(removed)

        if self._action == "insert":
            self._store.setdefault(self._table, []).append(self._upsert_payload or {})
            return FakeResponse([self._upsert_payload or {}])

        return FakeResponse([])


class FakeClient:
    """Builder-style Supabase fake that backs onto an in-memory dict store."""

    def __init__(
        self,
        store: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        recorder: Optional[Dict[str, Any]] = None,
    ):
        self._store = store if store is not None else {}
        self._recorder = recorder if recorder is not None else {}

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(name, self._store, self._recorder)


@contextmanager
def patched_supabase(store: Optional[Dict[str, List[Dict[str, Any]]]] = None):
    """Install the fake client at every import site the feature touches.

    Patches three sites because the repository, the router, and the
    processor each ``from src.supabase_client import get_service_client``
    at their own module scope.
    """
    recorder: Dict[str, Any] = {}
    client = FakeClient(store=store, recorder=recorder)

    def _factory():
        return client

    with patch("src.config_overrides.repository.get_service_client", _factory), \
         patch("src.config_overrides.router.get_service_client", _factory), \
         patch("src.processor.get_service_client", _factory):
        yield store if store is not None else client._store, recorder
