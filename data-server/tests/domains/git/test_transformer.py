"""Git-domain Transformer tests.

The Chunk-4 transformer accepts pre-built entity bundles and regroups them
into a :class:`TransformResult` (the raw-DTO path is deferred to Chunk 8 —
see module docstring). These tests exercise:

* happy path with the full set of entity buckets,
* ``source`` ClassVar and ``transform`` contract,
* missing project key / wrong project type are rejected,
* wrong entity types inside a bucket are rejected,
* unknown bundle keys are rejected,
* the NotImplementedError surface for unsupported raw shapes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import pytest

from src.common.domains import Transformer, TransformResult
from src.common.domains.git import (
    Change,
    ChangeType,
    Commit,
    File,
    GitAccount,
    GitProject,
    GitTransformer,
    Hunk,
    LineChange,
    LineOperation,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


def _build_entity_bundle() -> dict:
    project = GitProject(id="git-1", name="zep", source=SourceKind.GIT)
    project_ref = project.ref()
    alice = GitAccount(
        id=GitAccount.make_id("Alice", "a@x"),
        name="Alice",
        project_ref=project_ref,
        email="a@x",
    )
    commit = Commit(
        id="abc",
        sha="abc",
        project_ref=project_ref,
        message="m",
        author_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        committer_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_ref=alice.ref(),
        committer_ref=alice.ref(),
    )
    file = File(id="src/app.py", project_ref=project_ref, path="src/app.py", extension="py")
    change = Change(
        id=Change.make_id(commit.id, file.path, file.path),
        commit_ref=commit.ref(),
        file_ref=file.ref(),
        change_type=ChangeType.ADD,
        old_path=file.path,
        new_path=file.path,
    )
    hunk = Hunk(
        id=Hunk.make_id(change.id, 0),
        change_ref=change.ref(),
        ordinal=0,
        line_changes=[
            LineChange(
                operation=LineOperation.ADD,
                line_number=1,
                commit_ref=commit.ref(),
            )
        ],
    )
    return {
        "project": project,
        "accounts": [alice],
        "commits": [commit],
        "files": [file],
        "changes": [change],
        "hunks": [hunk],
    }


def test_git_transformer_is_a_transformer():
    assert issubclass(GitTransformer, Transformer)
    assert GitTransformer.source == SourceKind.GIT


def test_git_transformer_happy_path():
    bundle = _build_entity_bundle()
    result = GitTransformer().transform(bundle)
    assert isinstance(result, TransformResult)
    assert result.project is bundle["project"]
    # Every expected bucket is present (empty buckets too).
    assert set(result.entities) == {
        EntityKind.GIT_ACCOUNT,
        EntityKind.COMMIT,
        EntityKind.FILE,
        EntityKind.CHANGE,
        EntityKind.HUNK,
    }
    assert [e.id for e in result.entities[EntityKind.GIT_ACCOUNT]] == [
        GitAccount.make_id("Alice", "a@x")
    ]
    assert [e.id for e in result.entities[EntityKind.COMMIT]] == ["abc"]
    assert [e.id for e in result.entities[EntityKind.FILE]] == ["src/app.py"]
    assert len(result.entities[EntityKind.CHANGE]) == 1
    assert len(result.entities[EntityKind.HUNK]) == 1


def test_git_transformer_handles_missing_optional_buckets():
    bundle = _build_entity_bundle()
    del bundle["hunks"]
    del bundle["changes"]
    result = GitTransformer().transform(bundle)
    # Missing buckets default to an empty list — chunk 8 can iterate
    # uniformly regardless.
    assert result.entities[EntityKind.HUNK] == []
    assert result.entities[EntityKind.CHANGE] == []


def test_git_transformer_rejects_missing_project_key():
    with pytest.raises(ValueError, match="project"):
        GitTransformer().transform({"commits": []})


def test_git_transformer_rejects_wrong_project_type():
    with pytest.raises(TypeError, match="GitProject"):
        GitTransformer().transform({"project": "not-a-project"})


def test_git_transformer_rejects_wrong_entity_in_bucket():
    bundle = _build_entity_bundle()
    # File in the commits bucket — must be Commit.
    bundle["commits"] = [bundle["files"][0]]
    with pytest.raises(TypeError, match="commits"):
        GitTransformer().transform(bundle)


def test_git_transformer_rejects_unknown_bundle_keys():
    bundle = _build_entity_bundle()
    bundle["pull_requests"] = []
    with pytest.raises(ValueError, match="unknown bundle keys"):
        GitTransformer().transform(bundle)


def test_git_transformer_rejects_raw_dto_for_now():
    """Raw ``GitLogDTO`` ingestion is wired in Chunk 8 — Chunk 4 documents
    the boundary by raising NotImplementedError instead of silently
    accepting wrong inputs."""
    with pytest.raises(NotImplementedError, match="entity-bundle"):
        GitTransformer().transform(object())  # any non-Mapping input


# ---------------------------------------------------------------------------
# __init_subclass__ enforcement of `source` (review round-2 fix #1)
# ---------------------------------------------------------------------------


def test_transformer_concrete_subclass_without_source_is_rejected():
    """Mirrors ``Entity.__init_subclass__`` — concrete leaves must declare
    ``source``. Failure happens at class-definition time so Chunk 5/6
    authors get the error in the file they're editing, not at runtime."""
    with pytest.raises(TypeError, match="must declare"):

        class _BadTransformer(Transformer):  # noqa: F841 — must raise on class creation
            def transform(self, raw):  # type: ignore[override]
                return None


def test_transformer_concrete_subclass_with_source_works():
    """A correctly-declared concrete Transformer subclass loads cleanly
    and the ``source`` ClassVar reads back."""

    class _OkTransformer(Transformer):
        source: ClassVar[SourceKind] = SourceKind.JIRA

        def transform(self, raw):  # type: ignore[override]
            # Body is irrelevant for this test — we only verify the
            # __init_subclass__ check is satisfied.
            raise NotImplementedError

    assert _OkTransformer.source == SourceKind.JIRA
    assert _OkTransformer.__transformer_abstract__ is False


def test_transformer_abstract_subclass_may_omit_source():
    """Intermediate abstract bases opt out via ``abstract=True`` (mirrors
    the kernel pattern). A concrete leaf of that abstract base still has
    to declare ``source`` (otherwise the kernel enforcement is moot)."""

    class _Intermediate(Transformer, abstract=True):
        # Shared scaffolding, no source yet.
        pass

    assert _Intermediate.__transformer_abstract__ is True
    # Sanity: the intermediate base itself is class-creatable even without
    # ``source``. ABC machinery still prevents direct instantiation because
    # ``transform`` remains abstract.
    assert hasattr(_Intermediate, "__abstractmethods__")

    # Concrete leaf of the abstract base still needs ``source``.
    with pytest.raises(TypeError, match="must declare"):

        class _StillBad(_Intermediate):  # noqa: F841 — must raise on class creation
            def transform(self, raw):  # type: ignore[override]
                return None
