"""Git-domain registry tests.

Covers:

* every registry's CRUD surface (add/get/remove/contains/len),
* every declared index (lookup correctness, multi-key fan-out, ``None`` key
  skip semantics where applicable),
* pickle round-trip via :class:`Registry.dump` / ``load`` and indirectly via
  :class:`PickleStore` (Chunk 1).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.common.domains.git import (
    Change,
    ChangeType,
    Commit,
    File,
    GitAccount,
    GitAccountRegistry,
    GitProject,
    GitProjectRegistry,
    Hunk,
    LineChange,
    LineOperation,
    HunkRegistry,
    ChangeRegistry,
    CommitRegistry,
    FileRegistry,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind
from src.common.pickle_store import PickleStore


PROJECT_ID = "git-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _account(name: str, email: str, uu: str | None = None) -> GitAccount:
    return GitAccount(
        id=GitAccount.make_id(name, email),
        name=name,
        project_ref=PROJECT_REF,
        email=email,
        unified_user_id=uu,
    )


def _commit(
    sha: str,
    author: GitAccount,
    committer: GitAccount | None = None,
    parents: list[str] | None = None,
    project_ref: EntityRef = PROJECT_REF,
) -> Commit:
    parents = parents or []
    return Commit(
        id=sha,
        sha=sha,
        project_ref=project_ref,
        message=f"commit {sha}",
        author_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        committer_date=datetime(2024, 1, 2, tzinfo=timezone.utc),
        author_ref=author.ref(),
        committer_ref=(committer or author).ref(),
        parent_refs=[EntityRef(kind=EntityKind.COMMIT, id=p) for p in parents],
    )


def _file(path: str) -> File:
    return File(
        id=path,
        project_ref=PROJECT_REF,
        path=path,
        extension=File.derive_extension(path),
    )


def _change(commit: Commit, file: File, ctype: ChangeType) -> Change:
    cid = Change.make_id(commit.id, file.path, file.path)
    return Change(
        id=cid,
        commit_ref=commit.ref(),
        file_ref=file.ref(),
        change_type=ctype,
        old_path=file.path,
        new_path=file.path,
    )


def _hunk(change: Change, ordinal: int) -> Hunk:
    return Hunk(
        id=Hunk.make_id(change.id, ordinal),
        change_ref=change.ref(),
        ordinal=ordinal,
        line_changes=[
            LineChange(
                operation=LineOperation.ADD,
                line_number=1,
                commit_ref=change.commit_ref,
            )
        ],
    )


# ---------------------------------------------------------------------------
# GitProjectRegistry
# ---------------------------------------------------------------------------


def test_git_project_registry_add_and_by_name():
    reg = GitProjectRegistry()
    p1 = GitProject(id="git-1", name="zep", source=SourceKind.GIT)
    p2 = GitProject(id="git-2", name="zep", source=SourceKind.GIT)
    p3 = GitProject(id="git-3", name="other", source=SourceKind.GIT)
    reg.add(p1)
    reg.add(p2)
    reg.add(p3)
    assert len(reg) == 3
    assert reg.get("git-1") is p1
    assert {p.id for p in reg.by_name["zep"]} == {"git-1", "git-2"}
    assert {p.id for p in reg.by_name["other"]} == {"git-3"}


# ---------------------------------------------------------------------------
# GitAccountRegistry
# ---------------------------------------------------------------------------


def test_git_account_registry_indexes():
    reg = GitAccountRegistry()
    alice = _account("Alice", "alice@x.com", uu="uu-1")
    alice_alt = _account("Alice", "alice@other.com")  # no unified_user_id
    bob = _account("Bob", "bob@x.com", uu="uu-2")
    other_proj_ref = EntityRef(kind=EntityKind.PROJECT, id="git-2")
    carol = GitAccount(
        id=GitAccount.make_id("Carol", "carol@x.com"),
        name="Carol",
        project_ref=other_proj_ref,
        email="carol@x.com",
        unified_user_id="uu-1",
    )
    reg.add(alice)
    reg.add(alice_alt)
    reg.add(bob)
    reg.add(carol)

    # by_email
    assert {a.id for a in reg.by_email["alice@x.com"]} == {alice.id}
    # by_project
    assert {a.id for a in reg.by_project[PROJECT_REF]} == {
        alice.id,
        alice_alt.id,
        bob.id,
    }
    assert {a.id for a in reg.by_project[other_proj_ref]} == {carol.id}
    # by_unified_user — fans out, None keys skipped
    assert {a.id for a in reg.by_unified_user["uu-1"]} == {alice.id, carol.id}
    assert {a.id for a in reg.by_unified_user["uu-2"]} == {bob.id}
    # No entry under None — verifies "_account_unified_key returns None
    # → skipped" semantics from kernel _normalize_keys.
    assert reg.by_unified_user[None] == ()


def test_git_account_registry_remove_updates_indexes():
    reg = GitAccountRegistry()
    a = _account("A", "a@x", uu="uu-1")
    reg.add(a)
    assert reg.by_unified_user["uu-1"] == (a,)
    reg.remove(a.id)
    assert reg.by_unified_user["uu-1"] == ()
    assert reg.by_email["a@x"] == ()
    assert reg.by_project[PROJECT_REF] == ()


# ---------------------------------------------------------------------------
# CommitRegistry
# ---------------------------------------------------------------------------


def test_commit_registry_indexes():
    reg = CommitRegistry()
    alice = _account("A", "a@x")
    bob = _account("B", "b@x")
    c1 = _commit("c1", alice)
    c2 = _commit("c2", alice, committer=bob, parents=["c1"])
    c3 = _commit("c3", bob, parents=["c1", "c2"])
    reg.add(c1)
    reg.add(c2)
    reg.add(c3)

    # by_author / by_committer
    assert {c.id for c in reg.by_author[alice.ref()]} == {"c1", "c2"}
    assert {c.id for c in reg.by_author[bob.ref()]} == {"c3"}
    assert {c.id for c in reg.by_committer[alice.ref()]} == {"c1"}
    assert {c.id for c in reg.by_committer[bob.ref()]} == {"c2", "c3"}
    # by_project
    assert {c.id for c in reg.by_project[PROJECT_REF]} == {"c1", "c2", "c3"}

    # by_parent fans out — children of c1 are c2 and c3, children of c2 is c3.
    c1_ref = EntityRef(kind=EntityKind.COMMIT, id="c1")
    c2_ref = EntityRef(kind=EntityKind.COMMIT, id="c2")
    assert {c.id for c in reg.by_parent[c1_ref]} == {"c2", "c3"}
    assert {c.id for c in reg.by_parent[c2_ref]} == {"c3"}


# ---------------------------------------------------------------------------
# FileRegistry
# ---------------------------------------------------------------------------


def test_file_registry_indexes():
    reg = FileRegistry()
    f1 = _file("src/app.py")
    f2 = _file("src/util.py")
    f3 = _file("docs/intro.md")
    f4 = _file("README")  # no extension
    reg.add(f1)
    reg.add(f2)
    reg.add(f3)
    reg.add(f4)

    assert {f.id for f in reg.by_project[PROJECT_REF]} == {
        "src/app.py",
        "src/util.py",
        "docs/intro.md",
        "README",
    }
    assert {f.id for f in reg.by_extension["py"]} == {"src/app.py", "src/util.py"}
    assert {f.id for f in reg.by_extension["md"]} == {"docs/intro.md"}
    # README has no extension — `derive_extension` returns None → skipped.
    assert reg.by_extension[None] == ()


# ---------------------------------------------------------------------------
# ChangeRegistry
# ---------------------------------------------------------------------------


def test_change_registry_indexes():
    reg = ChangeRegistry()
    alice = _account("A", "a@x")
    c1 = _commit("c1", alice)
    c2 = _commit("c2", alice, parents=["c1"])
    f_app = _file("src/app.py")
    f_doc = _file("docs/x.md")

    ch1 = _change(c1, f_app, ChangeType.ADD)
    ch2 = _change(c2, f_app, ChangeType.MODIFY)
    ch3 = _change(c2, f_doc, ChangeType.ADD)
    reg.add(ch1)
    reg.add(ch2)
    reg.add(ch3)

    assert {c.id for c in reg.by_commit[c1.ref()]} == {ch1.id}
    assert {c.id for c in reg.by_commit[c2.ref()]} == {ch2.id, ch3.id}
    assert {c.id for c in reg.by_file[f_app.ref()]} == {ch1.id, ch2.id}
    assert {c.id for c in reg.by_file[f_doc.ref()]} == {ch3.id}
    assert {c.id for c in reg.by_change_type[ChangeType.ADD]} == {ch1.id, ch3.id}
    assert {c.id for c in reg.by_change_type[ChangeType.MODIFY]} == {ch2.id}


# ---------------------------------------------------------------------------
# HunkRegistry
# ---------------------------------------------------------------------------


def test_hunk_registry_indexes():
    reg = HunkRegistry()
    alice = _account("A", "a@x")
    c1 = _commit("c1", alice)
    f = _file("src/app.py")
    ch = _change(c1, f, ChangeType.ADD)
    h0 = _hunk(ch, 0)
    h1 = _hunk(ch, 1)
    reg.add(h0)
    reg.add(h1)

    assert {h.id for h in reg.by_change[ch.ref()]} == {h0.id, h1.id}
    assert len(reg) == 2


# ---------------------------------------------------------------------------
# Pickle round-trip via PickleStore — verifies indexes rebuild on load
# ---------------------------------------------------------------------------


def test_commit_registry_pickle_round_trip(tmp_path: Path):
    reg = CommitRegistry()
    alice = _account("A", "a@x")
    bob = _account("B", "b@x")
    reg.add(_commit("c1", alice))
    reg.add(_commit("c2", alice, parents=["c1"]))
    reg.add(_commit("c3", bob, parents=["c1"]))

    store = PickleStore(tmp_path)
    store.write_registry(EntityKind.COMMIT.value, reg)
    restored: CommitRegistry = store.read_registry(
        EntityKind.COMMIT.value, CommitRegistry
    )
    assert len(restored) == 3
    # Indexes rebuilt:
    c1_ref = EntityRef(kind=EntityKind.COMMIT, id="c1")
    assert {c.id for c in restored.by_parent[c1_ref]} == {"c2", "c3"}
    assert {c.id for c in restored.by_author[alice.ref()]} == {"c1", "c2"}


def test_change_registry_pickle_round_trip(tmp_path: Path):
    reg = ChangeRegistry()
    alice = _account("A", "a@x")
    c1 = _commit("c1", alice)
    f = _file("src/app.py")
    ch = _change(c1, f, ChangeType.ADD)
    reg.add(ch)
    store = PickleStore(tmp_path)
    store.write_registry(EntityKind.CHANGE.value, reg)
    restored = store.read_registry(EntityKind.CHANGE.value, ChangeRegistry)
    assert len(restored) == 1
    assert {c.id for c in restored.by_change_type[ChangeType.ADD]} == {ch.id}


def test_git_account_registry_pickle_round_trip(tmp_path: Path):
    reg = GitAccountRegistry()
    reg.add(_account("A", "a@x", uu="uu-1"))
    reg.add(_account("B", "b@x"))
    store = PickleStore(tmp_path)
    store.write_registry(EntityKind.GIT_ACCOUNT.value, reg)
    restored = store.read_registry(EntityKind.GIT_ACCOUNT.value, GitAccountRegistry)
    assert len(restored) == 2
    # by_unified_user reload also exercises the None-key skip path:
    assert {a.email for a in restored.by_unified_user["uu-1"]} == {"a@x"}
    assert restored.by_unified_user[None] == ()
