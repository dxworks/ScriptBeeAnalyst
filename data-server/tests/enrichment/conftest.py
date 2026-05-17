"""Shared fixture helpers for v2 enrichment tests.

The legacy ``tests/enrichment/fixtures.py`` (build_synthetic_graph, make_*)
constructed v1 ``GitProject`` / ``File`` / ``Change`` objects with Python
back-references. v2 lives on typed registries + ``EntityRef`` instead —
the helpers here mirror the legacy *intent* on the v2 shape so the
restored A2.x trait tests stay easy to read.

Pattern: each helper returns the entity it created. Caller is responsible
for adding it to the graph (or use :func:`add_*` variants that do both).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pytest

from src.common.domains.git.models import (
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
from src.common.kernel import EntityRef, Graph
from src.common.people import SourceKind


UTC = timezone.utc


# ----------------------------------------------------------------------
# Atomic factories
# ----------------------------------------------------------------------
def make_project(name: str = "synthetic") -> GitProject:
    return GitProject(id=f"gp:{name}", name=name, source=SourceKind.GIT)


def make_account(name: str, email: str, project_ref: EntityRef) -> GitAccount:
    return GitAccount(
        id=GitAccount.make_id(name, email),
        name=name,
        email=email,
        project_ref=project_ref,
    )


def make_file(path: str, project_ref: EntityRef) -> File:
    return File(
        id=path,
        path=path,
        project_ref=project_ref,
        extension=File.derive_extension(path),
    )


def make_commit(
    sha: str,
    message: str,
    author: GitAccount,
    when: datetime,
    project_ref: EntityRef,
    parents: Optional[list[EntityRef]] = None,
) -> Commit:
    return Commit(
        id=sha,
        project_ref=project_ref,
        message=message,
        author_date=when,
        committer_date=when,
        author_ref=author.ref(),
        committer_ref=author.ref(),
        parent_refs=parents or [],
    )


def make_change(
    commit: Commit,
    file_: File,
    added: int = 0,
    deleted: int = 0,
    change_type: ChangeType = ChangeType.MODIFY,
) -> tuple[Change, list[Hunk]]:
    """Construct a :class:`Change` + a single :class:`Hunk` carrying
    ``added`` + ``deleted`` :class:`LineChange` records.

    Returns ``(change, [hunk])`` so callers can register both.
    """
    change = Change(
        id=Change.make_id(commit.id, file_.path, file_.path),
        commit_ref=commit.ref(),
        file_ref=file_.ref(),
        change_type=change_type,
        old_path=file_.path,
        new_path=file_.path,
    )
    hunk = Hunk(
        id=Hunk.make_id(change.id, 0),
        change_ref=change.ref(),
        ordinal=0,
        line_changes=[
            LineChange(operation=LineOperation.ADD, line_number=i + 1, commit_ref=commit.ref())
            for i in range(added)
        ] + [
            LineChange(operation=LineOperation.DELETE, line_number=i + 1, commit_ref=commit.ref())
            for i in range(deleted)
        ],
    )
    # Hunk refs are wired on the change so consumers can walk them
    # without going through the by_change index when convenient.
    change.hunk_refs = [hunk.ref()]
    return change, [hunk]


# ----------------------------------------------------------------------
# Composite helpers — register-as-you-go
# ----------------------------------------------------------------------
def add_change(graph: Graph, commit: Commit, file_: File, **kwargs) -> Change:
    """Build + register a change + its hunk(s) on ``graph``. Returns the change."""
    change, hunks = make_change(commit, file_, **kwargs)
    graph.changes.add(change)
    for h in hunks:
        graph.hunks.add(h)
    return change


# ----------------------------------------------------------------------
# Top-level synthetic graph
# ----------------------------------------------------------------------
def build_v2_graph(project_name: str = "synthetic") -> tuple[Graph, GitProject]:
    """Empty v2 :class:`Graph` plus its registered :class:`GitProject`.

    Callers populate accounts / commits / files / changes themselves so
    each test stays explicit about the wiring it needs.
    """
    graph = Graph(project_id=project_name)
    project = make_project(project_name)
    graph.add_project(project)
    return graph, project


# ----------------------------------------------------------------------
# Pytest fixtures — convenient aliases for the test files.
# ----------------------------------------------------------------------
@pytest.fixture
def v2_graph() -> tuple[Graph, GitProject]:
    """A pristine :class:`Graph` + registered :class:`GitProject`."""
    return build_v2_graph()
