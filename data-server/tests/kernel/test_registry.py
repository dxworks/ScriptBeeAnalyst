"""Toy Entity + Registry with one declared index — full CRUD + reindex."""
from __future__ import annotations

from typing import ClassVar

import pytest

from src.common.kernel import (
    Entity,
    EntityKind,
    EntityRef,
    IndexSpec,
    Registry,
)


# ---- toy domain ----------------------------------------------------------


class _Commit(Entity):
    kind: ClassVar[EntityKind] = EntityKind.COMMIT
    author_ref: EntityRef
    files: list[EntityRef] = []


class _CommitRegistry(Registry[_Commit, str]):
    indexes = [
        IndexSpec(name="by_author", key_fn=lambda c: c.author_ref),
        IndexSpec(name="by_file", key_fn=lambda c: c.files),
    ]

    def get_id(self, entity: _Commit) -> str:
        return entity.id


# ---- helpers -------------------------------------------------------------

ALICE = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice")
BOB = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="bob")
FILE_A = EntityRef(kind=EntityKind.FILE, id="a.py")
FILE_B = EntityRef(kind=EntityKind.FILE, id="b.py")


def _registry_with_three() -> _CommitRegistry:
    reg = _CommitRegistry()
    reg.add(_Commit(id="c1", author_ref=ALICE, files=[FILE_A]))
    reg.add(_Commit(id="c2", author_ref=ALICE, files=[FILE_A, FILE_B]))
    reg.add(_Commit(id="c3", author_ref=BOB, files=[FILE_B]))
    return reg


# ---- tests ---------------------------------------------------------------


def test_add_get_len_iter():
    reg = _registry_with_three()
    assert len(reg) == 3
    assert {c.id for c in reg} == {"c1", "c2", "c3"}
    assert reg.get("c1").author_ref == ALICE
    assert reg.get("missing") is None
    assert "c2" in reg
    assert reg.ids() == {"c1", "c2", "c3"}
    assert {c.id for c in reg.all()} == {"c1", "c2", "c3"}


def test_index_lookup_single_key():
    reg = _registry_with_three()
    by_alice = reg.by_author[ALICE]
    assert {c.id for c in by_alice} == {"c1", "c2"}
    assert {c.id for c in reg.by_author[BOB]} == {"c3"}
    # Missing key returns empty tuple for multi-indexes.
    assert reg.by_author[EntityRef(kind=EntityKind.GIT_ACCOUNT, id="zzz")] == ()


def test_index_lookup_multi_key():
    reg = _registry_with_three()
    # c1 and c2 both touch FILE_A; c2 and c3 both touch FILE_B.
    assert {c.id for c in reg.by_file[FILE_A]} == {"c1", "c2"}
    assert {c.id for c in reg.by_file[FILE_B]} == {"c2", "c3"}


def test_remove_updates_indexes():
    reg = _registry_with_three()
    reg.remove("c2")
    assert len(reg) == 2
    assert reg.get("c2") is None
    assert {c.id for c in reg.by_author[ALICE]} == {"c1"}
    assert {c.id for c in reg.by_file[FILE_A]} == {"c1"}
    assert {c.id for c in reg.by_file[FILE_B]} == {"c3"}


def test_add_replaces_existing_entity_and_updates_indexes():
    reg = _registry_with_three()
    # Replace c1 — used to be by Alice on FILE_A; now by Bob on FILE_B.
    reg.add(_Commit(id="c1", author_ref=BOB, files=[FILE_B]))
    assert reg.get("c1").author_ref == BOB
    # Alice's bucket no longer has c1.
    assert {c.id for c in reg.by_author[ALICE]} == {"c2"}
    assert {c.id for c in reg.by_author[BOB]} == {"c1", "c3"}
    assert {c.id for c in reg.by_file[FILE_A]} == {"c2"}
    assert {c.id for c in reg.by_file[FILE_B]} == {"c1", "c2", "c3"}


def test_reindex_rebuilds_from_scratch():
    reg = _registry_with_three()
    # Mutate the underlying entity (it's a Pydantic model, allowed because
    # ``frozen=False`` on Entity). Indexes don't auto-track in-place edits;
    # reindex() is the recovery hook.
    c1 = reg.get("c1")
    assert c1 is not None
    c1.author_ref = BOB
    reg.reindex()
    assert {c.id for c in reg.by_author[ALICE]} == {"c2"}
    assert {c.id for c in reg.by_author[BOB]} == {"c1", "c3"}


def test_index_by_name_lookup():
    reg = _registry_with_three()
    idx = reg.index("by_author")
    assert ALICE in idx
    assert BOB in idx
    with pytest.raises(AttributeError):
        reg.index("does_not_exist")


def test_entity_forbids_extra_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _Commit(id="c", author_ref=ALICE, files=[], bogus=1)  # type: ignore[call-arg]


# ---- Optional fix: concrete Entity subclasses must declare ``kind`` -----


def test_concrete_entity_missing_kind_is_rejected():
    """A concrete leaf without ``kind`` fails at class definition time."""
    with pytest.raises(TypeError, match="must declare"):

        class _BadConcrete(Entity):  # noqa: F841 — definition itself must raise
            name: str


def test_intermediate_abstract_entity_may_skip_kind():
    """Intermediate bases (Account, Tag) opt out via ``abstract=True``."""

    class _AbstractAccount(Entity, abstract=True):
        name: str

    class _ConcreteAccount(_AbstractAccount):
        kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
        email: str

    inst = _ConcreteAccount(id="x", name="X", email="x@x")
    assert inst.kind == EntityKind.GIT_ACCOUNT
    assert inst.ref() == EntityRef(kind=EntityKind.GIT_ACCOUNT, id="x")


def test_concrete_subclass_of_abstract_without_kind_still_rejected():
    """Forgetting ``kind`` on a leaf of an abstract intermediate still
    raises — the opt-out only applies to the intermediate."""

    class _AbstractTag(Entity, abstract=True):
        target: EntityRef

    with pytest.raises(TypeError, match="must declare"):

        class _ForgotKind(_AbstractTag):  # noqa: F841
            pass
