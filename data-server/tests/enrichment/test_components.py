"""Component resolution + membership tests — restored from
``git show f840488^:data-server/tests/enrichment/test_components.py``
and ported to v2.

v1 → v2 port notes
------------------

* Legacy ``compute_enrichments(g, cfg).components`` is replaced by the
  v2 :class:`ComponentResolverMetric` which emits
  ``component_membership`` :class:`Relation` rows (one per file).
* Legacy ``ComponentMapping`` / ``ComponentResolver`` live unchanged
  under :mod:`src.common.domains.components.resolver` (Chunk 7 port).
* Legacy ``resolver.build_components(paths)`` is **not ported** — v2
  reads file membership from the relation set instead of constructing
  ``Component`` entities up front. The Chunk-8 ``ComponentRegistry`` is
  populated by a separate post-metric helper (see
  :mod:`src.enrichment.metrics.implementations.component_resolver`'s
  ``build_components_from_relations`` factory note in its docstring).

The pipeline-end tests use the synthetic v2 graph built by the
``conftest`` helpers; the resolver-only tests test ``ComponentResolver``
in isolation.
"""
from __future__ import annotations

import json

from src.common.domains.components.resolver import (
    OTHER_COMPONENT,
    ComponentMapping,
    ComponentResolver,
    ComponentSpec,
    load_component_mapping,
)
from src.common.kernel import EntityKind
from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import run_pipeline

from tests.enrichment.conftest import (
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)

from datetime import datetime, timedelta, timezone

UTC = timezone.utc


# ----------------------------------------------------------------------
# Resolver-only tests (no graph)
# ----------------------------------------------------------------------
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
    # Root-level path with no '/': falls into (other) when an explicit
    # mapping exists but doesn't cover it.
    assert resolver.resolve("README") == OTHER_COMPONENT


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


def test_prefix_for_returns_explicit_path_prefix():
    mapping = ComponentMapping(components={
        "core": ComponentSpec(path_prefix="src/foo/"),
    })
    r = ComponentResolver(mapping)
    assert r.prefix_for("core") == "src/foo/"
    # Heuristic mode: the name itself is the prefix.
    assert r.prefix_for("docs") == "docs"
    # Synthetic (other) bucket: empty prefix.
    assert r.prefix_for(OTHER_COMPONENT) == ""


# ----------------------------------------------------------------------
# Pipeline-level tests — v2 emits ``component_membership`` relations
# ----------------------------------------------------------------------
def _populate_synthetic_files(g, project, paths: list[str]) -> None:
    """Add minimal author + commit + change rows so the pipeline has
    something to walk, plus one File entity per path.
    """
    author = make_account("Synthetic", "syn@example.com", project.ref())
    g.git_accounts.add(author)
    now = datetime.now(UTC)
    commit = make_commit("c0", "init", author, now - timedelta(hours=1), project.ref())
    g.commits.add(commit)
    for path in paths:
        f = make_file(path, project.ref())
        g.files.add(f)
        add_change(g, commit, f, added=1)


def _membership_pairs(graph) -> dict[str, set[str]]:
    """Return ``{component_name: {file_path, ...}}`` from the relation set."""
    out: dict[str, set[str]] = {}
    for rel in graph.relations.of_kind("component_membership"):
        if rel.target.kind != EntityKind.COMPONENT:
            continue
        out.setdefault(rel.target.id, set()).add(rel.source.id)
    return out


def test_pipeline_emits_components_with_synthetic_graph_heuristic():
    """Heuristic mode — every file under ``src/`` rolls up to component ``src``."""
    g, p = build_v2_graph("components-h")
    _populate_synthetic_files(g, p, ["src/buggy.py", "src/owner.py", "src/orphan.py"])
    run_pipeline(g, EnrichmentConfig())
    members = _membership_pairs(g)
    assert "src" in members
    assert members["src"] == {"src/buggy.py", "src/owner.py", "src/orphan.py"}


def test_pipeline_uses_mapping_path_when_present(tmp_path):
    """Explicit mapping wins — only ``src/buggy*`` lands under
    ``buggy_corner``; the rest fall back to the top-folder heuristic.
    """
    payload = {
        "buggy_corner": {"path_prefix": "src/buggy"},
    }
    p = tmp_path / "m.json"
    p.write_text(json.dumps(payload))

    g, gp = build_v2_graph("components-m")
    _populate_synthetic_files(g, gp, ["src/buggy.py", "src/owner.py", "src/orphan.py"])
    cfg = EnrichmentConfig(components_mapping_path=str(p))
    g.__dict__["config"] = cfg
    run_pipeline(g, cfg)
    members = _membership_pairs(g)
    assert "buggy_corner" in members
    assert members["buggy_corner"] == {"src/buggy.py"}
    assert "src" in members  # owner.py + orphan.py fall to top-folder
    assert members["src"] == {"src/owner.py", "src/orphan.py"}
