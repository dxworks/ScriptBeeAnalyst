"""Component resolution: heuristic, mapping override, and the (other) bucket."""
from __future__ import annotations

import json
import os

from src.enrichment.components.mapping import (
    ComponentMapping,
    ComponentSpec,
    load_component_mapping,
)
from src.enrichment.components.resolver import ComponentResolver, OTHER_COMPONENT
from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import build_synthetic_graph


def test_top_folder_fallback_when_no_mapping():
    resolver = ComponentResolver(ComponentMapping())
    assert resolver.resolve("src/foo/bar.py") == "src"
    assert resolver.resolve("lib/x/y.py") == "lib"


def test_explicit_mapping_wins_over_top_folder():
    mapping = ComponentMapping(components={
        "core": ComponentSpec(path_prefix="src/foo/", extra_paths=["lib/foo-helpers/"]),
    })
    resolver = ComponentResolver(mapping)
    # Explicit prefix beats heuristic.
    assert resolver.resolve("src/foo/bar.py") == "core"
    # Extra path also recognised.
    assert resolver.resolve("lib/foo-helpers/util.py") == "core"
    # Anything else falls back to top-folder.
    assert resolver.resolve("docs/readme.md") == "docs"


def test_longest_prefix_wins():
    mapping = ComponentMapping(components={
        "outer": ComponentSpec(path_prefix="src/"),
        "inner": ComponentSpec(path_prefix="src/foo/"),
    })
    resolver = ComponentResolver(mapping)
    assert resolver.resolve("src/foo/x.py") == "inner"
    assert resolver.resolve("src/bar/x.py") == "outer"


def test_other_component_for_orphan_files_with_explicit_mapping():
    mapping = ComponentMapping(components={
        "core": ComponentSpec(path_prefix="src/"),
    })
    resolver = ComponentResolver(mapping)
    # Root-level path with no '/': falls into (other) when an explicit mapping
    # exists but doesn't cover it.
    assert resolver.resolve("README") == OTHER_COMPONENT


def test_build_components_groups_files():
    mapping = ComponentMapping(components={
        "core": ComponentSpec(path_prefix="src/"),
    })
    resolver = ComponentResolver(mapping)
    paths = ["src/a.py", "src/b.py", "docs/readme.md"]
    comps = resolver.build_components(paths)
    by_name = {c.name: c for c in comps}
    assert "core" in by_name and "docs" in by_name
    assert by_name["core"].file_paths == ["src/a.py", "src/b.py"]
    assert by_name["docs"].file_paths == ["docs/readme.md"]


def test_load_component_mapping_missing_returns_empty(tmp_path):
    m = load_component_mapping(str(tmp_path / "does-not-exist.json"))
    assert m.is_empty()


def test_load_component_mapping_invalid_json_returns_empty(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json")
    m = load_component_mapping(str(p))
    assert m.is_empty()


def test_load_component_mapping_round_trip(tmp_path):
    payload = {
        "core": {"path_prefix": "src/foo/", "extra_paths": ["lib/foo/"]},
        "ui":   {"path_prefix": "ui/"},
    }
    p = tmp_path / "components.mapping.json"
    p.write_text(json.dumps(payload))
    m = load_component_mapping(str(p))
    assert "core" in m.components
    assert m.components["core"].path_prefix == "src/foo/"
    assert m.components["core"].extra_paths == ["lib/foo/"]


def test_pipeline_emits_components_with_synthetic_graph():
    g = build_synthetic_graph()
    e = compute_enrichments(g, EnrichmentConfig())
    # All synthetic files live under `src/` -> single heuristic component.
    names = [c.name for c in e.components]
    assert names == ["src"]
    paths = e.components[0].file_paths
    # file_paths reflect actual registry, sorted.
    assert "src/buggy.py" in paths
    assert "src/owner.py" in paths
    assert "src/orphan.py" in paths


def test_pipeline_uses_mapping_path_when_present(tmp_path):
    payload = {
        "buggy_corner": {"path_prefix": "src/buggy"},
    }
    p = tmp_path / "m.json"
    p.write_text(json.dumps(payload))
    g = build_synthetic_graph()
    cfg = EnrichmentConfig(components_mapping_path=str(p))
    e = compute_enrichments(g, cfg)
    names = sorted(c.name for c in e.components)
    # `src/buggy.py` -> buggy_corner; the others fall back to top-folder `src`.
    assert "buggy_corner" in names
    assert "src" in names
    by_name = {c.name: c for c in e.components}
    assert by_name["buggy_corner"].file_paths == ["src/buggy.py"]
