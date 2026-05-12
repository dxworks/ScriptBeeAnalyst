"""UnifiedUser + UnifiedUserRegistry — index, for_account, dangling refs."""
from __future__ import annotations

from typing import ClassVar

import pytest

from src.common.kernel import EntityKind, EntityRef, Graph, Registry
from src.common.people import Account, SourceKind, UnifiedUser, UnifiedUserRegistry


PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id="proj-1")


# ---- dummy concrete Account subclass used across the test file ---------
#
# We intentionally roll our own here so this test never imports a real
# domain class (Chunk 4+'s GitAccount). That keeps the people module
# decoupled the way the chunk-02 design says it should be.


class _DummyAccount(Account):
    kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
    email: str

    @property
    def source(self) -> SourceKind:
        return SourceKind.GIT


class _DummyAccountRegistry(Registry[_DummyAccount, str]):
    def get_id(self, entity: _DummyAccount) -> str:
        return entity.id


def _account(id_: str, email: str) -> _DummyAccount:
    return _DummyAccount(
        id=id_,
        name=email.split("@")[0].title(),
        project_ref=PROJECT_REF,
        email=email,
    )


# ---- registry is instantiable (no abstract `kind` on the registry) -----


def test_unified_user_registry_instantiable():
    """The registry class itself has no abstract requirement — the kind
    lives on UnifiedUser, not on UnifiedUserRegistry."""
    reg = UnifiedUserRegistry()
    assert len(reg) == 0


# ---- core add/remove behavior ------------------------------------------


def test_unified_user_add_and_get():
    reg = UnifiedUserRegistry()
    uu = UnifiedUser(
        id="uu-1",
        display_name="Alice",
        primary_email="alice@example.com",
        account_refs=[],
    )
    reg.add(uu)
    assert reg.get("uu-1") is uu
    assert len(reg) == 1
    assert "uu-1" in reg


def test_unified_user_remove():
    reg = UnifiedUserRegistry()
    reg.add(UnifiedUser(id="uu-1", display_name="Alice"))
    reg.add(UnifiedUser(id="uu-2", display_name="Bob"))
    removed = reg.remove("uu-1")
    assert removed is not None
    assert removed.id == "uu-1"
    assert reg.get("uu-1") is None
    assert len(reg) == 1


# ---- by_account index fans out across account_refs ---------------------


def test_by_account_index_fans_out_over_multiple_refs():
    alice_git = _account("alice-git", "alice@example.com")
    alice_jira_ref = EntityRef(kind=EntityKind.JIRA_USER, id="alice-jira")

    reg = UnifiedUserRegistry()
    uu = UnifiedUser(
        id="uu-1",
        display_name="Alice",
        primary_email="alice@example.com",
        account_refs=[alice_git.ref(), alice_jira_ref],
    )
    reg.add(uu)

    # Index has one bucket per ref, both containing the same UnifiedUser.
    assert {u.id for u in reg.by_account[alice_git.ref()]} == {"uu-1"}
    assert {u.id for u in reg.by_account[alice_jira_ref]} == {"uu-1"}

    # Missing key returns an empty tuple per kernel multi-index semantics.
    other = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="ghost")
    assert reg.by_account[other] == ()


def test_for_account_returns_canonical_unified_user():
    """``for_account`` is the documented Chunk-7/9 entry point."""
    alice_git = _account("alice-git", "alice@example.com")
    bob_git = _account("bob-git", "bob@example.com")

    reg = UnifiedUserRegistry()
    reg.add(
        UnifiedUser(
            id="uu-alice",
            display_name="Alice",
            account_refs=[alice_git.ref()],
        )
    )
    reg.add(
        UnifiedUser(
            id="uu-bob",
            display_name="Bob",
            account_refs=[bob_git.ref()],
        )
    )

    assert reg.for_account(alice_git.ref()).id == "uu-alice"
    assert reg.for_account(bob_git.ref()).id == "uu-bob"


def test_for_account_returns_none_when_account_unknown():
    """No matching UnifiedUser → ``None`` (the smart-merge UI uses this
    as the "no merge accepted yet" signal)."""
    reg = UnifiedUserRegistry()
    reg.add(
        UnifiedUser(
            id="uu-1",
            display_name="Alice",
            account_refs=[EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice")],
        )
    )
    missing = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="never-merged")
    assert reg.for_account(missing) is None


def test_for_account_handles_deleted_account_gracefully():
    """If the account_ref points at a now-deleted account, ``for_account``
    still returns the owning UnifiedUser — the registry doesn't (and
    shouldn't) auto-prune dangling refs. Callers resolve through
    :meth:`UnifiedUser.accounts` if they want live entities.

    Chunk 8 update: the Graph has typed registry fields now, so we use
    the real :class:`GitAccountRegistry` here. The test still exercises
    the dangling-ref semantic that matters for UnifiedUser.
    """
    from src.common.domains.git.models import GitAccount
    from src.common.domains.git.registries import GitAccountRegistry

    alice_git = GitAccount(
        id="alice-git",
        name="Alice",
        project_ref=PROJECT_REF,
        email="alice@example.com",
    )
    alice_ref = alice_git.ref()
    accounts = GitAccountRegistry()
    accounts.add(alice_git)

    uu_reg = UnifiedUserRegistry()
    uu_reg.add(
        UnifiedUser(
            id="uu-1",
            display_name="Alice",
            account_refs=[alice_ref],
        )
    )

    accounts.remove("alice-git")
    # The UnifiedUser still owns the (now-dangling) ref:
    assert uu_reg.for_account(alice_ref).id == "uu-1"

    # ...and resolving through the graph yields an empty live-account list.
    graph = Graph(
        project_id="p",
        git_accounts=accounts,
        unified_users=uu_reg,
    )
    uu = uu_reg.get("uu-1")
    assert uu is not None
    assert uu.accounts(graph) == []  # the dangling ref resolves to None


# ---- UnifiedUser.accounts_of_kind --------------------------------------


def test_accounts_of_kind_filters_by_entity_kind():
    """The generic accessor that replaces git_accounts/jira_users/github_users.

    Chunk 8 update: typed Graph fields — use the real
    :class:`GitAccountRegistry` for the kind under test.
    """
    from src.common.domains.git.models import GitAccount
    from src.common.domains.git.registries import GitAccountRegistry

    alice_git = GitAccount(
        id="alice-git",
        name="Alice",
        project_ref=PROJECT_REF,
        email="alice@example.com",
    )
    accounts = GitAccountRegistry()
    accounts.add(alice_git)

    alice_jira_ref = EntityRef(kind=EntityKind.JIRA_USER, id="alice-jira")
    uu = UnifiedUser(
        id="uu-1",
        display_name="Alice",
        account_refs=[alice_git.ref(), alice_jira_ref],
    )
    uu_reg = UnifiedUserRegistry()
    uu_reg.add(uu)

    graph = Graph(
        project_id="p",
        git_accounts=accounts,
        unified_users=uu_reg,
    )

    git_accounts = uu.accounts_of_kind(graph, EntityKind.GIT_ACCOUNT)
    assert [a.id for a in git_accounts] == ["alice-git"]

    # The jira ref filters in but resolves to None (no Jira registry bound,
    # so the typed field is the empty default registry).
    jira_users = uu.accounts_of_kind(graph, EntityKind.JIRA_USER)
    assert jira_users == []
