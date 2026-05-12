"""EntityRef contract: equality, hashability, JSON roundtrip, resolve()."""
from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import ValidationError

from src.common.kernel import Entity, EntityKind, EntityRef, Graph, Registry


# ---- toy entities & registry used across the suite ----------------------


class _Person(Entity):
    kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
    name: str


class _PersonRegistry(Registry[_Person, str]):
    def get_id(self, entity: _Person) -> str:
        return entity.id


# ---- tests --------------------------------------------------------------


def test_ref_equality_and_hashable():
    a = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice")
    b = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice")
    c = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="bob")

    assert a == b
    assert a != c
    # Hashable -> usable as dict / set keys.
    assert {a, b, c} == {a, c}
    assert {a: 1}[b] == 1


def test_ref_frozen():
    ref = EntityRef(kind=EntityKind.COMMIT, id="abc")
    with pytest.raises(ValidationError):
        ref.id = "def"  # type: ignore[misc]


def test_ref_json_roundtrip():
    ref = EntityRef(kind=EntityKind.COMMIT, id="c1")
    payload = ref.model_dump_json()
    assert "commit" in payload  # uses the StrEnum value
    restored = EntityRef.model_validate_json(payload)
    assert restored == ref


def test_ref_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        EntityRef(kind="not_a_kind", id="x")  # type: ignore[arg-type]


def test_entity_ref_method_uses_classvar():
    alice = _Person(id="alice", name="Alice")
    assert alice.ref() == EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice")


def test_resolve_through_graph():
    reg = _PersonRegistry()
    reg.add(_Person(id="alice", name="Alice"))
    graph = Graph(project_id="p1", registries={EntityKind.GIT_ACCOUNT: reg})

    ref = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice")
    resolved = ref.resolve(graph)
    assert resolved is not None
    assert resolved.id == "alice"

    missing = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="ghost").resolve(graph)
    assert missing is None

    no_registry = EntityRef(kind=EntityKind.COMMIT, id="x").resolve(graph)
    assert no_registry is None
