"""Role-typed account-ref marker + ``AccountRoleRegistry`` (task P1.B).

Verifies the contract added in ``kernel/role_ref.py`` and the class-init
hook in ``kernel/entity.py``:

* :func:`account_role_ref` (singular) and :func:`account_role_refs`
  (plural) mark a Pydantic ``EntityRef`` field with ``_account_role``
  metadata in ``json_schema_extra``.
* ``Entity.__pydantic_init_subclass__`` walks ``model_fields`` and
  registers every marker with :data:`AccountRoleRegistry`.
* ``AccountRoleRegistry.for_entity(cls)`` returns the right specs.

The test deliberately defines synthetic ``Entity`` subclasses inline —
it must not import any real domain models so its assertions don't
depend on the broader domain ``RoleRefSpec`` count.
"""
from __future__ import annotations

from typing import ClassVar, List

from src.common.kernel import (
    AccountRoleRegistry,
    Entity,
    EntityKind,
    EntityRef,
    account_role_ref,
    account_role_refs,
)


def test_singular_marker_registers_spec():
    class _Foo(Entity):
        kind: ClassVar[EntityKind] = EntityKind.COMMIT
        author_ref: EntityRef = account_role_ref("author")

    specs = AccountRoleRegistry.for_entity(_Foo)
    assert len(specs) == 1
    spec = specs[0]
    assert spec.owning_cls is _Foo
    assert spec.field_name == "author_ref"
    assert spec.role == "author"
    assert spec.plural is False


def test_plural_marker_registers_spec():
    class _Bar(Entity):
        kind: ClassVar[EntityKind] = EntityKind.PULL_REQUEST
        assignee_refs: List[EntityRef] = account_role_refs("assignee")

    specs = AccountRoleRegistry.for_entity(_Bar)
    assert len(specs) == 1
    spec = specs[0]
    assert spec.owning_cls is _Bar
    assert spec.field_name == "assignee_refs"
    assert spec.role == "assignee"
    assert spec.plural is True


def test_plural_marker_defaults_to_empty_list():
    """A field built with :func:`account_role_refs` has
    ``default_factory=list`` so omitting it deserializes to ``[]``."""

    class _Baz(Entity):
        kind: ClassVar[EntityKind] = EntityKind.PULL_REQUEST
        assignee_refs: List[EntityRef] = account_role_refs("assignee")

    inst = _Baz(id="b1")
    assert inst.assignee_refs == []


def test_multiple_markers_on_one_class():
    """An entity with two role-typed fields registers two specs."""

    class _Quux(Entity):
        kind: ClassVar[EntityKind] = EntityKind.COMMIT
        author_ref: EntityRef = account_role_ref("author")
        committer_ref: EntityRef = account_role_ref("committer")

    specs = AccountRoleRegistry.for_entity(_Quux)
    by_field = {s.field_name: s for s in specs}
    assert set(by_field) == {"author_ref", "committer_ref"}
    assert by_field["author_ref"].role == "author"
    assert by_field["committer_ref"].role == "committer"
    assert all(s.plural is False for s in specs)


def test_unmarked_ref_fields_are_not_registered():
    """A plain ``EntityRef`` field without the marker stays invisible
    to the registry — only opt-in fields participate."""

    class _Plain(Entity):
        kind: ClassVar[EntityKind] = EntityKind.COMMIT
        author_ref: EntityRef = account_role_ref("author")
        # parent_refs is a normal ref-list, NOT a role-typed account ref
        parent_refs: List[EntityRef] = []

    specs = AccountRoleRegistry.for_entity(_Plain)
    field_names = [s.field_name for s in specs]
    assert "author_ref" in field_names
    assert "parent_refs" not in field_names
