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
    parse_component_mapping,
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


# ----------------------------------------------------------------------
# B2 — parse_component_mapping (dict-based) + data-over-path precedence
# ----------------------------------------------------------------------
def test_parse_component_mapping_happy_path():
    raw = {
        "core": {"path_prefix": "src/foo/", "extra_paths": ["lib/foo/"]},
        "ui":   {"path_prefix": "ui/"},
    }
    m = parse_component_mapping(raw)
    assert set(m.components) == {"core", "ui"}
    assert m.components["core"].path_prefix == "src/foo/"
    assert m.components["core"].extra_paths == ["lib/foo/"]
    assert m.components["ui"].path_prefix == "ui/"
    assert m.components["ui"].extra_paths == []


def test_parse_component_mapping_malformed_drops_bad_entries():
    raw = {
        "core":     {"path_prefix": "src/foo/"},           # kept
        "broken":   "not-a-dict",                          # dropped
        "no_prefix": {"extra_paths": ["x/"]},              # dropped (missing path_prefix)
        "empty_prefix": {"path_prefix": ""},               # dropped (empty path_prefix)
        "bad_prefix": {"path_prefix": 123},                # dropped (non-str)
        "ui":       {"path_prefix": "ui/", "extra_paths": "not-a-list"},  # extras coerced to []
        "mixed":    {"path_prefix": "src/m/", "extra_paths": ["a/", 99, "b/"]},  # 99 dropped
    }
    m = parse_component_mapping(raw)
    assert set(m.components) == {"core", "ui", "mixed"}
    assert m.components["ui"].extra_paths == []
    assert m.components["mixed"].extra_paths == ["a/", "b/"]


def test_parse_component_mapping_non_mapping_returns_empty():
    assert parse_component_mapping(None).is_empty()
    assert parse_component_mapping([{"path_prefix": "x/"}]).is_empty()
    assert parse_component_mapping("oops").is_empty()


def test_load_and_parse_agree_on_same_payload(tmp_path):
    """``load_component_mapping`` delegates to ``parse_component_mapping`` —
    both must produce structurally identical mappings.
    """
    payload = {
        "core": {"path_prefix": "src/foo/", "extra_paths": ["lib/foo/"]},
        "ui":   {"path_prefix": "ui/"},
    }
    p = tmp_path / "m.json"
    p.write_text(json.dumps(payload))
    from_file = load_component_mapping(str(p))
    from_dict = parse_component_mapping(payload)
    assert from_file.components.keys() == from_dict.components.keys()
    for name, spec in from_file.components.items():
        peer = from_dict.components[name]
        assert spec.path_prefix == peer.path_prefix
        assert spec.extra_paths == peer.extra_paths


def test_pipeline_uses_mapping_data_when_present():
    """``components_mapping_data`` (per-project dict) wins over the
    heuristic when no path is set."""
    g, gp = build_v2_graph("components-data")
    _populate_synthetic_files(
        g, gp, ["src/buggy.py", "src/owner.py", "src/orphan.py"]
    )
    cfg = EnrichmentConfig(
        components_mapping_data={"buggy_corner": {"path_prefix": "src/buggy"}}
    )
    g.__dict__["config"] = cfg
    run_pipeline(g, cfg)
    members = _membership_pairs(g)
    assert members.get("buggy_corner") == {"src/buggy.py"}
    assert members.get("src") == {"src/owner.py", "src/orphan.py"}


def test_pipeline_prefers_mapping_data_over_mapping_path(tmp_path):
    """When both are set, ``components_mapping_data`` wins — the dict is
    per-project, the path is operator-level fallback."""
    # Path mapping would group everything as ``everything``.
    path_payload = {"everything": {"path_prefix": "src/"}}
    p = tmp_path / "m.json"
    p.write_text(json.dumps(path_payload))
    # Data mapping says ``buggy_corner`` for src/buggy* only.
    data_payload = {"buggy_corner": {"path_prefix": "src/buggy"}}

    g, gp = build_v2_graph("components-precedence")
    _populate_synthetic_files(
        g, gp, ["src/buggy.py", "src/owner.py", "src/orphan.py"]
    )
    cfg = EnrichmentConfig(
        components_mapping_path=str(p),
        components_mapping_data=data_payload,
    )
    g.__dict__["config"] = cfg
    run_pipeline(g, cfg)
    members = _membership_pairs(g)
    # Data won: ``everything`` never appears, only buggy_corner + top-folder.
    assert "everything" not in members
    assert members.get("buggy_corner") == {"src/buggy.py"}
    assert members.get("src") == {"src/owner.py", "src/orphan.py"}


def test_pipeline_falls_back_to_path_when_data_is_empty(tmp_path):
    """An empty / falsy ``components_mapping_data`` triggers the path
    fallback (covers the ``if data:`` guard in the resolver helpers).
    """
    path_payload = {"buggy_corner": {"path_prefix": "src/buggy"}}
    p = tmp_path / "m.json"
    p.write_text(json.dumps(path_payload))

    g, gp = build_v2_graph("components-empty-data")
    _populate_synthetic_files(
        g, gp, ["src/buggy.py", "src/owner.py", "src/orphan.py"]
    )
    cfg = EnrichmentConfig(
        components_mapping_path=str(p),
        components_mapping_data={},  # explicit empty dict — falsy, falls through
    )
    g.__dict__["config"] = cfg
    run_pipeline(g, cfg)
    members = _membership_pairs(g)
    assert members.get("buggy_corner") == {"src/buggy.py"}
