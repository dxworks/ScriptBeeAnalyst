"""B2 — per-project component_mapping fetch path.

Asserts that :func:`processor.fetch_project_component_mapping` returns
whatever the Supabase row's ``component_mapping`` column carries, and
that :func:`processor.build_graph` injects it into ``EnrichmentConfig``
so the resolver picks it up.

The Supabase client is monkey-patched at the
``src.processor.get_service_client`` import site (the processor
re-exports the symbol from ``src.supabase_client``); no real network
call is made.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from src.enrichment.config import DEFAULT_CONFIG, EnrichmentConfig
from src import processor


# ---------------------------------------------------------------------------
# Supabase fake — minimal surface the fetch helper actually touches.
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, data: List[Dict[str, Any]]):
        self.data = data


class _FakeQuery:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _FakeResponse(self._rows)


class _FakeTable:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def __call__(self):
        return _FakeQuery(self._rows)


class _FakeClient:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def table(self, _name: str):
        return _FakeQuery(self._rows)


def _patched_service_client(rows: List[Dict[str, Any]]):
    return patch.object(
        processor, "get_service_client", return_value=_FakeClient(rows)
    )


# ---------------------------------------------------------------------------
# fetch_project_component_mapping
# ---------------------------------------------------------------------------
def test_fetch_returns_mapping_when_column_populated():
    mapping = {"core": {"path_prefix": "src/foo/"}}
    with _patched_service_client([{"component_mapping": mapping}]):
        out = processor.fetch_project_component_mapping("proj-1")
    assert out == mapping


def test_fetch_returns_none_when_no_row():
    with _patched_service_client([]):
        out = processor.fetch_project_component_mapping("missing-id")
    assert out is None


def test_fetch_returns_none_when_column_null():
    with _patched_service_client([{"component_mapping": None}]):
        out = processor.fetch_project_component_mapping("null-col")
    assert out is None


def test_fetch_returns_none_when_supabase_raises():
    """Network / auth error → caller falls back to path; no raise."""
    def _boom():
        raise RuntimeError("supabase unreachable")

    with patch.object(processor, "get_service_client", side_effect=_boom):
        out = processor.fetch_project_component_mapping("doesnt-matter")
    assert out is None


# ---------------------------------------------------------------------------
# build_graph wiring — fetched mapping ends up on EnrichmentConfig.
# ---------------------------------------------------------------------------
def test_build_graph_injects_mapping_data_into_effective_config(monkeypatch):
    """``build_graph`` clones the caller's config with the fetched mapping
    and forwards it to :func:`build_graph_from_bundles`.
    """
    seen_config: Dict[str, Optional[EnrichmentConfig]] = {"value": None}

    mapping = {"core": {"path_prefix": "src/foo/"}}

    def _fake_download(_project_id: str):
        return processor.DownloadedFiles()

    def _fake_bundles(_downloaded, project_name="Project"):
        return {}

    def _fake_build_from_bundles(project_id, bundles, *, config=None):
        seen_config["value"] = config
        # Return a minimal stub; build_graph doesn't introspect it beyond
        # the dump call below.
        from src.common.kernel import Graph
        from src.enrichment.pipeline import PipelineResult
        return Graph(project_id=project_id), PipelineResult(
            builders_run=[], metrics_run=[], errors=[]
        )

    def _fake_save(_graph):
        from pathlib import Path
        return Path("/tmp/noop")

    monkeypatch.setattr(processor, "download_serialized_files_from_supabase", _fake_download)
    monkeypatch.setattr(processor, "_downloaded_files_to_bundles", _fake_bundles)
    monkeypatch.setattr(processor, "build_graph_from_bundles", _fake_build_from_bundles)
    monkeypatch.setattr(processor, "save_graph_to_disk", _fake_save)

    with _patched_service_client([{"component_mapping": mapping}]):
        processor.build_graph("proj-with-mapping", project_name="Demo")

    cfg = seen_config["value"]
    assert cfg is not None
    assert cfg.components_mapping_data == mapping


def test_build_graph_leaves_config_unchanged_when_no_mapping(monkeypatch):
    """No row / null column → ``effective_config`` is the caller's config
    unmodified (so the path fallback path stays viable).
    """
    seen_config: Dict[str, Optional[EnrichmentConfig]] = {"value": None}
    caller_cfg = replace(DEFAULT_CONFIG, components_mapping_path="/etc/x.json")

    def _fake_download(_project_id: str):
        return processor.DownloadedFiles()

    def _fake_bundles(_downloaded, project_name="Project"):
        return {}

    def _fake_build_from_bundles(project_id, bundles, *, config=None):
        seen_config["value"] = config
        from src.common.kernel import Graph
        from src.enrichment.pipeline import PipelineResult
        return Graph(project_id=project_id), PipelineResult(
            builders_run=[], metrics_run=[], errors=[]
        )

    def _fake_save(_graph):
        from pathlib import Path
        return Path("/tmp/noop")

    monkeypatch.setattr(processor, "download_serialized_files_from_supabase", _fake_download)
    monkeypatch.setattr(processor, "_downloaded_files_to_bundles", _fake_bundles)
    monkeypatch.setattr(processor, "build_graph_from_bundles", _fake_build_from_bundles)
    monkeypatch.setattr(processor, "save_graph_to_disk", _fake_save)

    with _patched_service_client([{"component_mapping": None}]):
        processor.build_graph("proj-no-mapping", project_name="Demo", config=caller_cfg)

    cfg = seen_config["value"]
    assert cfg is caller_cfg
    assert cfg.components_mapping_data is None
    assert cfg.components_mapping_path == "/etc/x.json"
