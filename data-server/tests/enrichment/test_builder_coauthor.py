"""Substantive port test for :class:`CoauthorBuilder`.

Synthetic mini-graph: 3 files, 2 git accounts, 3 commits. Verifies:

* Coauthor pair count equals number of files BOTH authors touched.
* No emissions when authors touched disjoint file sets.
* Lifetime/Recent windows respond to the host's ``recent_cutoff``.
* Canonical id is stable so re-running the builder doesn't churn the
  registry.

The synthetic host is the tiniest possible :class:`PipelineHost` —
it carries only ``files``, ``commits``, ``changes``, and the three
target registries. Indexes are wired via :class:`CommitRegistry` /
:class:`ChangeRegistry` / :class:`FileRegistry` (the v2 builders read
``by_file`` on changes).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from src.common.domains.git import (
    Change,
    ChangeRegistry,
    ChangeType,
    Commit,
    CommitRegistry,
    File,
    FileRegistry,
    GitAccount,
    GitAccountRegistry,
)
from src.common.kernel import EntityKind, EntityRef
from src.enrichment.relations import (
    Relation,
    RelationRegistry,
    WindowKind,
)
from src.enrichment.relations.implementations.coauthor import CoauthorBuilder
from src.enrichment.tags import ClassifierRegistry, TraitRegistry


# ----------------------------------------------------------------------
# Tiny synthetic host
# ----------------------------------------------------------------------
@dataclass
class _Host:
    files: FileRegistry
    commits: CommitRegistry
    changes: ChangeRegistry
    accounts: GitAccountRegistry
    relations: RelationRegistry
    traits: TraitRegistry
    classifiers: ClassifierRegistry
    recent_cutoff: Optional[datetime] = None
    config: Any = None


_PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id="proj-1")


def _make_account(name: str, email: str) -> GitAccount:
    return GitAccount(
        id=GitAccount.make_id(name, email),
        name=name,
        email=email,
        project_ref=_PROJECT_REF,
    )


def _make_file(path: str) -> File:
    return File(
        id=path,
        path=path,
        project_ref=_PROJECT_REF,
        extension=File.derive_extension(path),
    )


def _make_commit(
    sha: str,
    *,
    author: GitAccount,
    when: datetime,
    parents: Optional[list[EntityRef]] = None,
) -> Commit:
    return Commit(
        id=sha,
        sha=sha,
        project_ref=_PROJECT_REF,
        message=f"commit {sha}",
        author_date=when,
        committer_date=when,
        author_ref=author.ref(),
        committer_ref=author.ref(),
        parent_refs=parents or [],
    )


def _make_change(commit: Commit, file_: File, ordinal: int = 0) -> Change:
    cid = Change.make_id(commit.id, file_.path, file_.path)
    return Change(
        id=cid,
        commit_ref=commit.ref(),
        file_ref=file_.ref(),
        change_type=ChangeType.MODIFY,
        old_path=file_.path,
        new_path=file_.path,
    )


@pytest.fixture
def host() -> _Host:
    h = _Host(
        files=FileRegistry(),
        commits=CommitRegistry(),
        changes=ChangeRegistry(),
        accounts=GitAccountRegistry(),
        relations=RelationRegistry(),
        traits=TraitRegistry(),
        classifiers=ClassifierRegistry(),
    )
    return h


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
def test_coauthor_emits_pair_for_shared_files(host: _Host) -> None:
    """Two authors both touch two files → one coauthor edge, strength=2."""
    alice = _make_account("Alice", "alice@example.com")
    bob = _make_account("Bob", "bob@example.com")
    host.accounts.add(alice)
    host.accounts.add(bob)

    f1 = _make_file("a.py")
    f2 = _make_file("b.py")
    host.files.add(f1)
    host.files.add(f2)

    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    c1 = _make_commit("c1", author=alice, when=when)
    c2 = _make_commit("c2", author=bob, when=when)
    c3 = _make_commit("c3", author=alice, when=when)
    c4 = _make_commit("c4", author=bob, when=when)
    for c in (c1, c2, c3, c4):
        host.commits.add(c)

    # Alice touches a.py + b.py; Bob touches a.py + b.py.
    host.changes.add(_make_change(c1, f1))
    host.changes.add(_make_change(c2, f1))
    host.changes.add(_make_change(c3, f2))
    host.changes.add(_make_change(c4, f2))

    builder = CoauthorBuilder()
    relations = list(builder.build(host))

    # No recent cutoff → only LIFETIME emitted.
    assert len(relations) == 1
    rel = relations[0]
    assert rel.relation_kind == "coauthor"
    assert rel.window == WindowKind.LIFETIME
    assert rel.strength == 2.0
    # Endpoints are the two account refs.
    assert {rel.source, rel.target} == {alice.ref(), bob.ref()}


def test_coauthor_emits_nothing_when_authors_disjoint(host: _Host) -> None:
    """Authors touch disjoint file sets → no shared-file count → no edge."""
    alice = _make_account("Alice", "alice@example.com")
    bob = _make_account("Bob", "bob@example.com")
    host.accounts.add(alice)
    host.accounts.add(bob)

    f1 = _make_file("a.py")
    f2 = _make_file("b.py")
    host.files.add(f1)
    host.files.add(f2)

    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    c1 = _make_commit("c1", author=alice, when=when)
    c2 = _make_commit("c2", author=bob, when=when)
    host.commits.add(c1)
    host.commits.add(c2)
    host.changes.add(_make_change(c1, f1))
    host.changes.add(_make_change(c2, f2))

    relations = list(CoauthorBuilder().build(host))
    assert relations == []


def test_coauthor_recent_window_respects_cutoff(host: _Host) -> None:
    """With a recent cutoff, only commits past the cutoff land in RECENT."""
    alice = _make_account("Alice", "alice@example.com")
    bob = _make_account("Bob", "bob@example.com")
    host.accounts.add(alice)
    host.accounts.add(bob)
    f = _make_file("a.py")
    host.files.add(f)

    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    new = datetime(2024, 1, 1, tzinfo=timezone.utc)
    c1 = _make_commit("c1", author=alice, when=old)
    c2 = _make_commit("c2", author=bob, when=old)
    c3 = _make_commit("c3", author=alice, when=new)
    c4 = _make_commit("c4", author=bob, when=new)
    for c in (c1, c2, c3, c4):
        host.commits.add(c)
    for c in (c1, c2, c3, c4):
        host.changes.add(_make_change(c, f))

    host.recent_cutoff = new - timedelta(days=1)

    relations = list(CoauthorBuilder().build(host))
    # Should emit a LIFETIME and a RECENT edge — same pair, two windows.
    windows = sorted(r.window.value for r in relations)
    assert windows == ["lifetime", "recent"]
    by_win = {r.window: r for r in relations}
    assert by_win[WindowKind.LIFETIME].strength == 1.0  # one shared file
    assert by_win[WindowKind.RECENT].strength == 1.0


def test_coauthor_canonical_id_is_stable(host: _Host) -> None:
    """Running the builder twice produces the same ids — registry dedup works."""
    alice = _make_account("Alice", "alice@example.com")
    bob = _make_account("Bob", "bob@example.com")
    host.accounts.add(alice)
    host.accounts.add(bob)
    f = _make_file("a.py")
    host.files.add(f)
    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    c1 = _make_commit("c1", author=alice, when=when)
    c2 = _make_commit("c2", author=bob, when=when)
    host.commits.add(c1)
    host.commits.add(c2)
    host.changes.add(_make_change(c1, f))
    host.changes.add(_make_change(c2, f))

    builder = CoauthorBuilder()
    ids_first = [r.id for r in builder.build(host)]
    ids_second = [r.id for r in builder.build(host)]
    assert ids_first == ids_second
    # The canonical id matches Relation.canonical_id with sorted endpoints.
    src, tgt = sorted((alice.ref(), bob.ref()), key=lambda r: (r.kind, r.id))
    assert ids_first == [
        Relation.canonical_id(src, tgt, "coauthor", WindowKind.LIFETIME)
    ]


def test_coauthor_handles_empty_host_gracefully(host: _Host) -> None:
    """No files / no commits → empty output, no exception."""
    relations = list(CoauthorBuilder().build(host))
    assert relations == []
