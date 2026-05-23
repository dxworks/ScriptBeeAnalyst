"""Unit tests for :func:`build_components_from_relations`.

The helper lives next to :class:`ComponentResolverMetric` in
:mod:`src.enrichment.metrics.implementations.component_resolver`. It runs
post-pipeline (in :func:`src.processor.build_graph_from_bundles`) to
materialise :class:`Component` entities from the
``component_membership`` :class:`Relation` rows the metric emitted.

These tests exercise the helper directly against a hand-rolled graph + a
hand-rolled relation set so they're decoupled from the metric itself —
the upstream tests in ``test_components.py`` already cover the metric's
relation emissions, and exercising both layers from one test would
double-count failures.
"""
from __future__ import annotations

from src.common.domains.git.models import GitProject
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind
from src.enrichment.metrics.implementations.component_resolver import (
    build_components_from_relations,
)
from src.enrichment.relations import Relation, WindowKind

from tests.enrichment.conftest import build_v2_graph, make_file


def _add_membership(
    graph,
    file_ref: EntityRef,
    component_name: str,
    path_prefix: str,
) -> Relation:
    """Hand-build + register a single ``component_membership`` relation."""
    comp_ref = EntityRef(kind=EntityKind.COMPONENT, id=component_name)
    rid = Relation.canonical_id(
        file_ref, comp_ref, "component_membership", WindowKind.LIFETIME
    )
    rel = Relation(
        id=rid,
        source=file_ref,
        target=comp_ref,
        relation_kind="component_membership",
        window=WindowKind.LIFETIME,
        strength=1.0,
        extras={"path_prefix": path_prefix},
    )
    graph.relations.add(rel)
    return rel


def test_aggregates_multiple_files_into_one_component():
    """Two files under ``src/foo/`` collapse to one :class:`Component`."""
    g, p = build_v2_graph("agg")
    f1 = make_file("src/foo/a.py", p.ref())
    f2 = make_file("src/foo/b.py", p.ref())
    g.files.add(f1)
    g.files.add(f2)
    _add_membership(g, f1.ref(), "foo", "src/foo/")
    _add_membership(g, f2.ref(), "foo", "src/foo/")

    built = build_components_from_relations(g)

    assert len(built) == 1
    assert len(g.components.all()) == 1
    comp = g.components.get("foo")
    assert comp is not None
    assert comp.name == "foo"
    assert comp.id == "foo"
    assert comp.path_prefix == "src/foo/"
    assert set(comp.file_refs) == {f1.ref(), f2.ref()}
    assert comp.description is None


def test_distinct_components_get_distinct_entries():
    """Files mapping to different components land in different registry rows."""
    g, p = build_v2_graph("distinct")
    f1 = make_file("src/foo/a.py", p.ref())
    f2 = make_file("src/bar/b.py", p.ref())
    g.files.add(f1)
    g.files.add(f2)
    _add_membership(g, f1.ref(), "foo", "src/foo/")
    _add_membership(g, f2.ref(), "bar", "src/bar/")

    build_components_from_relations(g)

    assert {c.name for c in g.components.all()} == {"foo", "bar"}
    assert g.components.get("foo").file_refs == [f1.ref()]
    assert g.components.get("bar").file_refs == [f2.ref()]


def test_path_prefix_lifted_from_relation_extras():
    """``path_prefix`` is read from ``rel.extras`` — no resolver needed."""
    g, p = build_v2_graph("prefix")
    f = make_file("custom/path/x.py", p.ref())
    g.files.add(f)
    _add_membership(g, f.ref(), "custom-component", "custom/path/")

    build_components_from_relations(g)

    comp = g.components.get("custom-component")
    assert comp is not None
    assert comp.path_prefix == "custom/path/"


def test_project_ref_is_most_common_across_files():
    """When files span projects, the most-common project wins."""
    g, p_a = build_v2_graph("multi-a")
    # A second project on the same graph so we can mix file ownership.
    p_b = GitProject(id="gp:multi-b", name="multi-b", source=SourceKind.GIT)
    g.add_project(p_b)

    f_a1 = make_file("src/a1.py", p_a.ref())
    f_a2 = make_file("src/a2.py", p_a.ref())
    f_b1 = make_file("src/b1.py", p_b.ref())
    for f in (f_a1, f_a2, f_b1):
        g.files.add(f)

    _add_membership(g, f_a1.ref(), "src", "src/")
    _add_membership(g, f_a2.ref(), "src", "src/")
    _add_membership(g, f_b1.ref(), "src", "src/")

    build_components_from_relations(g)

    comp = g.components.get("src")
    assert comp is not None
    # Two files in project A vs one in project B → A wins.
    assert comp.project_ref == p_a.ref()


def test_project_ref_none_when_no_files_resolve():
    """Unresolvable file refs (no matching File entity) leave project_ref None."""
    g, p = build_v2_graph("missing")
    # Build a ref for a file we DON'T register on graph.files.
    ghost_ref = EntityRef(kind=EntityKind.FILE, id="phantom/file.py")
    _add_membership(g, ghost_ref, "phantom", "phantom/")

    build_components_from_relations(g)

    comp = g.components.get("phantom")
    assert comp is not None
    # Ref preserved verbatim even when the file can't be resolved — the
    # registry indexes still get a sane key.
    assert comp.file_refs == [ghost_ref]
    assert comp.project_ref is None


def test_registry_indexes_are_live():
    """``by_name`` / ``by_file`` / ``by_project`` indexes populate on add."""
    g, p = build_v2_graph("indexes")
    f = make_file("src/a.py", p.ref())
    g.files.add(f)
    _add_membership(g, f.ref(), "src", "src/")

    build_components_from_relations(g)

    # by_name: lookup by human-readable name.
    by_name = g.components.by_name["src"]
    assert len(by_name) == 1
    assert by_name[0].name == "src"
    # by_file: given a file ref, find owning components.
    by_file = g.components.by_file[f.ref()]
    assert len(by_file) == 1
    assert by_file[0].id == "src"
    # by_project: every component anchored to that project.
    by_project = g.components.by_project[p.ref()]
    assert len(by_project) == 1


def test_idempotent_on_re_run():
    """Re-running the helper on the same relations keeps the registry stable."""
    g, p = build_v2_graph("idem")
    f = make_file("src/a.py", p.ref())
    g.files.add(f)
    _add_membership(g, f.ref(), "src", "src/")

    build_components_from_relations(g)
    first = list(g.components.all())
    build_components_from_relations(g)
    second = list(g.components.all())

    assert len(first) == 1
    assert len(second) == 1
    # Same ids — last writer wins (per Registry.add contract).
    assert {c.id for c in first} == {c.id for c in second}


def test_non_component_relations_are_ignored():
    """Only ``component_membership`` rows targeting COMPONENT refs count."""
    g, p = build_v2_graph("noise")
    f = make_file("src/a.py", p.ref())
    g.files.add(f)
    _add_membership(g, f.ref(), "src", "src/")

    # Add a noise relation of a different kind — must be ignored.
    other_ref = EntityRef(kind=EntityKind.FILE, id="src/b.py")
    noise_id = Relation.canonical_id(
        f.ref(), other_ref, "cochange", WindowKind.LIFETIME
    )
    g.relations.add(
        Relation(
            id=noise_id,
            source=f.ref(),
            target=other_ref,
            relation_kind="cochange",
            window=WindowKind.LIFETIME,
            strength=0.5,
        )
    )

    build_components_from_relations(g)

    assert {c.id for c in g.components.all()} == {"src"}
