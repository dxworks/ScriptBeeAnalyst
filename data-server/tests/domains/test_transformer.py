"""Shared :class:`Transformer.collect_bundle` helper tests.

The Chunk 4 review (item #2) flagged that each domain's
``_transform_entity_bundle`` skeleton (project-key check, project-type check,
unknown-key rejection, per-bucket type check) is going to be cloned three
times — once per domain. Chunk 5 promoted that skeleton to a single
``Transformer.collect_bundle`` classmethod so Git/Jira/GitHub all call it.

These tests exercise the helper directly through a tiny fixture transformer
so the contract is verified independently of any domain-specific quirks.
The Git/Jira/GitHub transformer tests then verify each domain wires it up
correctly with its own bucket specs.
"""
from __future__ import annotations

from typing import Any, ClassVar

import pytest

from src.common.domains import Transformer, TransformResult
from src.common.kernel import Entity, EntityKind, EntityRef
from src.common.people import SourceKind
from src.common.projects import Project


# ---------------------------------------------------------------------------
# Fixture domain — minimal Project + two Entity subclasses used to exercise
# ``collect_bundle`` without coupling to a real domain. Living here keeps the
# test self-contained; nothing from this file is exported anywhere else.
# ---------------------------------------------------------------------------


class _FakeProject(Project):
    """Concrete Project used only by these tests."""

    kind: ClassVar[EntityKind] = EntityKind.PROJECT

    def transformer_class(self) -> type["_FakeTransformer"]:  # type: ignore[override]
        return _FakeTransformer


class _FakeNode(Entity):
    """Stand-in for any domain entity bucketed under a generic EntityKind.

    We reuse :data:`EntityKind.COMPONENT` purely because it's a kernel-level
    enum member with no domain dependency. The bucket specs map keys to
    `(kind, cls)` pairs, so the choice of kind is irrelevant to the
    helper's contract.
    """

    kind: ClassVar[EntityKind] = EntityKind.COMPONENT

    project_ref: EntityRef


class _OtherNode(Entity):
    """A second entity class so we can exercise wrong-bucket-type errors."""

    kind: ClassVar[EntityKind] = EntityKind.TRAIT

    project_ref: EntityRef


_BUCKET_SPECS: dict[str, tuple[EntityKind, type[Entity]]] = {
    "nodes": (EntityKind.COMPONENT, _FakeNode),
    "others": (EntityKind.TRAIT, _OtherNode),
}


class _FakeTransformer(Transformer):
    """Concrete transformer that just forwards to ``collect_bundle``."""

    source: ClassVar[SourceKind] = SourceKind.APP_INSPECTOR

    def transform(self, raw: Any) -> TransformResult:
        return self.collect_bundle(raw, _FakeProject, _BUCKET_SPECS)


def _make_project() -> _FakeProject:
    return _FakeProject(
        id="p1", name="proj", source=SourceKind.APP_INSPECTOR
    )


# ---------------------------------------------------------------------------
# collect_bundle — happy path and validation
# ---------------------------------------------------------------------------


def test_collect_bundle_happy_path_with_all_buckets():
    project = _make_project()
    n1 = _FakeNode(id="n1", project_ref=project.ref())
    o1 = _OtherNode(id="o1", project_ref=project.ref())
    result = _FakeTransformer().transform(
        {"project": project, "nodes": [n1], "others": [o1]}
    )
    assert isinstance(result, TransformResult)
    assert result.project is project
    # Every declared bucket is present in the result (one per spec).
    assert set(result.entities) == {EntityKind.COMPONENT, EntityKind.TRAIT}
    assert result.entities[EntityKind.COMPONENT] == [n1]
    assert result.entities[EntityKind.TRAIT] == [o1]


def test_collect_bundle_missing_optional_buckets_default_to_empty():
    project = _make_project()
    result = _FakeTransformer().transform({"project": project})
    # All declared buckets present with empty lists — the dispatcher can
    # iterate uniformly regardless of which buckets were sent.
    assert result.entities[EntityKind.COMPONENT] == []
    assert result.entities[EntityKind.TRAIT] == []


def test_collect_bundle_rejects_missing_project_key():
    with pytest.raises(ValueError, match="'project'"):
        _FakeTransformer().transform({"nodes": []})


def test_collect_bundle_rejects_wrong_project_type():
    with pytest.raises(TypeError, match="_FakeProject"):
        _FakeTransformer().transform({"project": "not-a-project"})


def test_collect_bundle_rejects_unknown_bundle_keys():
    project = _make_project()
    with pytest.raises(ValueError, match="unknown bundle keys"):
        _FakeTransformer().transform(
            {"project": project, "nodes": [], "garbage": []}
        )


def test_collect_bundle_rejects_wrong_type_in_bucket():
    project = _make_project()
    o1 = _OtherNode(id="o1", project_ref=project.ref())
    with pytest.raises(TypeError, match="nodes"):
        # An _OtherNode in the 'nodes' bucket — must be _FakeNode.
        _FakeTransformer().transform({"project": project, "nodes": [o1]})


def test_collect_bundle_error_message_carries_transformer_name():
    """Errors mention the concrete transformer class, not the abstract
    base — Chunk 5 / 6 / 8 authors must be able to grep their way back to
    the bundle that triggered the error."""
    with pytest.raises(ValueError, match="_FakeTransformer.transform"):
        _FakeTransformer().transform({"nodes": []})
