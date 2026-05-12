"""Git-domain entity construction tests.

Covers:

* every entity class instantiates with valid fields,
* cross-entity references are :class:`EntityRef` (never Python refs),
* ``extra="forbid"`` rejects unknown attributes (inherited from Entity),
* class attributes propagate (``kind``, ``source``),
* derived helpers (``File.derive_extension``, ``GitAccount.make_id``,
  ``Change.make_id``, ``Hunk.make_id``, ``Hunk.added_lines`` /
  ``deleted_lines``) behave as documented.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.common.domains.git import (
    Change,
    ChangeType,
    Commit,
    File,
    GitAccount,
    GitProject,
    Hunk,
    LineChange,
    LineOperation,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "git-proj-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)


# ---------------------------------------------------------------------------
# GitProject
# ---------------------------------------------------------------------------


def test_git_project_construct_and_metadata():
    proj = GitProject(id=PROJECT_ID, name="zeppelin", source=SourceKind.GIT)
    assert proj.id == PROJECT_ID
    assert proj.kind == EntityKind.PROJECT
    assert proj.source == SourceKind.GIT
    assert proj.name == "zeppelin"
    assert proj.linked_project_ids == []
    # ref() routes through ClassVar kind:
    assert proj.ref() == EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)


def test_git_project_transformer_class_returns_git_transformer():
    from src.common.domains.git.transformer import GitTransformer

    proj = GitProject(id=PROJECT_ID, name="zeppelin", source=SourceKind.GIT)
    assert proj.transformer_class() is GitTransformer


def test_git_project_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        GitProject(
            id=PROJECT_ID,
            name="z",
            source=SourceKind.GIT,
            mystery=1,  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# GitAccount
# ---------------------------------------------------------------------------


def test_git_account_construct_with_class_attr_source():
    acct = GitAccount(
        id=GitAccount.make_id("Alice", "alice@example.com"),
        name="Alice",
        project_ref=PROJECT_REF,
        email="alice@example.com",
    )
    assert acct.kind == EntityKind.GIT_ACCOUNT
    assert acct.source == SourceKind.GIT
    assert acct.id == "Alice <alice@example.com>"
    assert acct.email == "alice@example.com"
    assert acct.unified_user_id is None
    assert acct.ref() == EntityRef(
        kind=EntityKind.GIT_ACCOUNT, id="Alice <alice@example.com>"
    )


def test_git_account_make_id_matches_legacy_format():
    """Legacy str(GitAccountId) was ``f"{name} <{email}>"`` — keep parity."""
    assert (
        GitAccount.make_id("Bob Builder", "bob@example.com")
        == "Bob Builder <bob@example.com>"
    )


def test_git_account_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        GitAccount(
            id="x",
            name="x",
            project_ref=PROJECT_REF,
            email="x@x",
            commits=[],  # legacy field that v2 dropped
        )


def test_git_account_unified_user_id_round_trip():
    acct = GitAccount(
        id="x",
        name="x",
        project_ref=PROJECT_REF,
        email="x@x",
        unified_user_id="uu-1",
    )
    payload = acct.model_dump()
    restored = GitAccount.model_validate(payload)
    assert restored.unified_user_id == "uu-1"
    # source remains the class attr after round-trip:
    assert restored.source == SourceKind.GIT


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def _alice_ref() -> EntityRef:
    return EntityRef(
        kind=EntityKind.GIT_ACCOUNT, id=GitAccount.make_id("A", "a@x")
    )


def _bob_ref() -> EntityRef:
    return EntityRef(
        kind=EntityKind.GIT_ACCOUNT, id=GitAccount.make_id("B", "b@x")
    )


def test_commit_construct_with_entity_refs():
    parent_ref = EntityRef(kind=EntityKind.COMMIT, id="parent-sha")
    c = Commit(
        id="abc123",
        project_ref=PROJECT_REF,
        message="init",
        author_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        committer_date=datetime(2024, 1, 2, tzinfo=timezone.utc),
        author_ref=_alice_ref(),
        committer_ref=_alice_ref(),
        parent_refs=[parent_ref],
        branch_id=1,
        repo_size=42,
    )
    assert c.kind == EntityKind.COMMIT
    assert c.id == "abc123"
    assert c.author_ref == _alice_ref()
    assert c.committer_ref == _alice_ref()
    assert c.parent_refs == [parent_ref]
    assert c.branch_id == 1
    assert c.repo_size == 42


def test_commit_does_not_accept_python_author_object():
    """Cross-entity refs must be EntityRef — never a Python object."""
    acct = GitAccount(
        id="x", name="X", project_ref=PROJECT_REF, email="x@x"
    )
    with pytest.raises(ValidationError):
        Commit(
            id="c1",
            project_ref=PROJECT_REF,
            message="m",
            author_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            committer_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            author_ref=acct,  # type: ignore[arg-type]
            committer_ref=_alice_ref(),
        )


def test_commit_rejects_legacy_changes_field():
    """The legacy ``changes: List[Change]`` field is gone in v2 — by-commit
    index covers the reverse lookup. Forbidding it here documents the
    contract."""
    with pytest.raises(ValidationError):
        Commit(
            id="c1",
            project_ref=PROJECT_REF,
            message="m",
            author_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            committer_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            author_ref=_alice_ref(),
            committer_ref=_alice_ref(),
            changes=[],  # type: ignore[call-arg]
        )


def test_commit_default_parents_empty_list():
    c = Commit(
        id="root",
        project_ref=PROJECT_REF,
        message="root",
        author_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        committer_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_ref=_alice_ref(),
        committer_ref=_alice_ref(),
    )
    assert c.parent_refs == []
    assert c.branch_id == 0
    assert c.repo_size == 0


# ---------------------------------------------------------------------------
# File
# ---------------------------------------------------------------------------


def test_file_construct_with_path_id():
    f = File(
        id="src/app.py",
        project_ref=PROJECT_REF,
        path="src/app.py",
        is_binary=False,
        extension="py",
    )
    assert f.kind == EntityKind.FILE
    assert f.id == "src/app.py"
    assert f.path == "src/app.py"
    assert f.is_binary is False
    assert f.extension == "py"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("src/app.py", "py"),
        ("foo.tar.gz", "gz"),
        ("README", None),
        (".gitignore", None),
        ("a/.hidden", None),
        ("docs/intro.md", "md"),
        ("", None),
    ],
)
def test_file_derive_extension(path, expected):
    assert File.derive_extension(path) == expected


# ---------------------------------------------------------------------------
# Change + Hunk + LineChange
# ---------------------------------------------------------------------------


def _commit_ref(sha: str = "abc") -> EntityRef:
    return EntityRef(kind=EntityKind.COMMIT, id=sha)


def _file_ref(path: str = "src/app.py") -> EntityRef:
    return EntityRef(kind=EntityKind.FILE, id=path)


def test_change_construct_and_id_helper():
    cid = Change.make_id("abc", "src/old.py", "src/new.py")
    assert cid == "abc-src/old.py->src/new.py"
    ch = Change(
        id=cid,
        commit_ref=_commit_ref("abc"),
        file_ref=_file_ref("src/new.py"),
        change_type=ChangeType.RENAME,
        old_path="src/old.py",
        new_path="src/new.py",
    )
    assert ch.kind == EntityKind.CHANGE
    assert ch.parent_commit_ref is None
    assert ch.parent_change_ref is None
    assert ch.hunk_refs == []


def test_change_rejects_python_file_ref():
    f = File(
        id="src/app.py",
        project_ref=PROJECT_REF,
        path="src/app.py",
    )
    with pytest.raises(ValidationError):
        Change(
            id="c1-old->new",
            commit_ref=_commit_ref(),
            file_ref=f,  # type: ignore[arg-type]
            change_type=ChangeType.MODIFY,
            old_path="o",
            new_path="n",
        )


def test_change_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        Change(
            id="x",
            commit_ref=_commit_ref(),
            file_ref=_file_ref(),
            change_type=ChangeType.MODIFY,
            old_path="o",
            new_path="n",
            annotated_lines=[],  # legacy field that v2 dropped
        )


def test_line_change_value_object_is_frozen():
    lc = LineChange(
        operation=LineOperation.ADD,
        line_number=10,
        commit_ref=_commit_ref(),
    )
    assert lc.operation == LineOperation.ADD
    # Value object is frozen — line objects are equal by value.
    same = LineChange(
        operation=LineOperation.ADD,
        line_number=10,
        commit_ref=_commit_ref(),
    )
    assert lc == same
    # Frozen → mutation raises.
    with pytest.raises(ValidationError):
        lc.line_number = 11  # type: ignore[misc]


def test_hunk_id_helper_and_partition_helpers():
    change_id = Change.make_id("c1", "src/x.py", "src/x.py")
    hid = Hunk.make_id(change_id, 0)
    assert hid == f"{change_id}#0"
    h = Hunk(
        id=hid,
        change_ref=EntityRef(kind=EntityKind.CHANGE, id=change_id),
        ordinal=0,
        line_changes=[
            LineChange(
                operation=LineOperation.DELETE,
                line_number=1,
                commit_ref=_commit_ref(),
            ),
            LineChange(
                operation=LineOperation.ADD,
                line_number=2,
                commit_ref=_commit_ref(),
            ),
            LineChange(
                operation=LineOperation.ADD,
                line_number=3,
                commit_ref=_commit_ref(),
            ),
        ],
    )
    assert h.kind == EntityKind.HUNK
    assert len(h.added_lines) == 2
    assert len(h.deleted_lines) == 1
    assert [lc.line_number for lc in h.added_lines] == [2, 3]
