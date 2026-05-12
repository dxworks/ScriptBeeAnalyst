"""Account base — abstractness, kind-enforcement, source-enforcement,
unified_user_id round-trip."""
from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import ValidationError

from src.common.kernel import EntityKind, EntityRef
from src.common.people import Account, SourceKind


PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id="proj-1")


def test_account_concrete_subclass_without_kind_is_rejected():
    """A concrete leaf of Account must declare ``kind`` — enforced by
    Chunk 1's ``Entity.__init_subclass__``.

    Note: we MUST override ``source`` here, otherwise the kernel sees the
    class as still abstract (``__abstractmethods__`` non-empty) and skips
    the kind check. The point of this test is the kind check itself.
    """
    with pytest.raises(TypeError, match="must declare"):

        class _BadAccount(Account):  # noqa: F841 — definition itself must raise
            email: str

            @property
            def source(self) -> SourceKind:
                return SourceKind.GIT


def test_account_concrete_subclass_without_source_cannot_be_instantiated():
    """A concrete leaf of Account that declares ``kind`` but forgets
    ``source`` is still abstract — Python's ABC machinery refuses to
    instantiate it. Class definition itself succeeds (kernel treats it as
    abstract because ``__abstractmethods__`` contains ``source``).
    """

    class _MissingSource(Account):
        kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
        email: str

    # Class is defined fine — but treated as abstract.
    assert "source" in getattr(_MissingSource, "__abstractmethods__", set())

    with pytest.raises(TypeError, match="abstract"):
        _MissingSource(
            id="x", name="X", project_ref=PROJECT_REF, email="x@x"
        )


def test_account_concrete_subclass_with_kind_and_source_works():
    """A correctly-declared concrete Account subclass instantiates fine."""

    class _GitAccount(Account):
        kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
        email: str

        @property
        def source(self) -> SourceKind:
            return SourceKind.GIT

    inst = _GitAccount(
        id="alice",
        name="Alice",
        project_ref=PROJECT_REF,
        email="alice@example.com",
    )
    assert inst.kind == EntityKind.GIT_ACCOUNT
    assert inst.id == "alice"
    assert inst.name == "Alice"
    assert inst.project_ref == PROJECT_REF
    assert inst.email == "alice@example.com"
    # source reads back via the property — no graph required.
    assert inst.source == SourceKind.GIT
    # Default is None — the smart-merge UI fills it later.
    assert inst.unified_user_id is None
    # ref() resolves via the ClassVar.
    assert inst.ref() == EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice")


def test_account_unified_user_id_round_trip():
    """``unified_user_id`` is a plain Optional[str] field — set/read/dump."""

    class _JiraUser(Account):
        kind: ClassVar[EntityKind] = EntityKind.JIRA_USER
        key: str

        @property
        def source(self) -> SourceKind:
            return SourceKind.JIRA

    user = _JiraUser(
        id="jira-1",
        name="Alice",
        project_ref=PROJECT_REF,
        key="alice.smith",
        unified_user_id="uu-1",
    )
    assert user.unified_user_id == "uu-1"

    # JSON round-trip preserves it.
    payload = user.model_dump()
    restored = _JiraUser.model_validate(payload)
    assert restored.unified_user_id == "uu-1"
    # source still reads back on the restored instance.
    assert restored.source == SourceKind.JIRA

    # Mutating it works (frozen=False on Entity).
    user.unified_user_id = "uu-2"
    assert user.unified_user_id == "uu-2"


def test_account_forbids_extra_fields():
    """``extra='forbid'`` on Entity propagates to Account subclasses."""

    class _GitAccount(Account):
        kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
        email: str

        @property
        def source(self) -> SourceKind:
            return SourceKind.GIT

    with pytest.raises(ValidationError):
        _GitAccount(
            id="x",
            name="X",
            project_ref=PROJECT_REF,
            email="x@x",
            bogus="value",  # type: ignore[call-arg]
        )


def test_account_is_marked_abstract():
    """The intermediate base opted out of kind via ``abstract=True``."""
    # The kernel stashes this flag on every Entity subclass.
    assert getattr(Account, "__entity_abstract__", False) is True
    # And ``source`` is in the abstract-methods set, enforcing
    # per-subclass declaration via Python's ABC machinery.
    assert "source" in getattr(Account, "__abstractmethods__", set())
