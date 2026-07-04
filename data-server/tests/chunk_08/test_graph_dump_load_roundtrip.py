"""Graph.dump → PickleStore → Graph.lazy round-trip.

Builds a tiny three-entity graph (1 commit, 1 file, 1 issue), dumps it
via :meth:`Graph.dump`, then lazily reloads it via :meth:`Graph.lazy`
and asserts each entity round-trips via :meth:`Graph.resolve`. Covers
the §8.1 per-registry pickle layout and the §1.6 typed-field contract.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.common.domains.git.models import (
    Commit,
    File,
    GitAccount,
    GitProject,
)
from src.common.domains.jira.models import (
    Issue,
    IssueStatus,
    IssueType,
    JiraProject,
    JiraUser,
)
from src.common.kernel import EntityKind, EntityRef, Graph
from src.common.people import SourceKind
from src.common.pickle_store import LazyRegistryProxy, PickleStore


def _build_small_graph() -> Graph:
    g = Graph(project_id="round-trip")

    # Git half.
    git_project = GitProject(id="gp1", name="zep", source=SourceKind.GIT)
    git_ref = git_project.ref()
    alice = GitAccount(
        id="alice",
        name="Alice",
        project_ref=git_ref,
        email="alice@x",
    )
    file = File(
        id="src/app.py",
        project_ref=git_ref,
        path="src/app.py",
        extension="py",
    )
    commit = Commit(
        id="abc",
        sha="abc",
        project_ref=git_ref,
        message="initial",
        author_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        committer_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_ref=alice.ref(),
        committer_ref=alice.ref(),
    )

    g.add_project(git_project)
    g.git_accounts.add(alice)
    g.files.add(file)
    g.commits.add(commit)

    # Jira half.
    jira_project = JiraProject(id="jp1", name="JIR", source=SourceKind.JIRA)
    jira_ref = jira_project.ref()
    status = IssueStatus(
        id="open",
        project_ref=jira_ref,
        name="Open",
        category="new",
    )
    bug_type = IssueType(
        id="bug",
        project_ref=jira_ref,
        name="Bug",
    )
    bob_jira = JiraUser(
        id="bob-jira",
        name="Bob",
        project_ref=jira_ref,
        key="bob",
        link="https://jira/bob",
    )
    issue = Issue(
        id="JIR-1",
        project_ref=jira_ref,
        key="JIR-1",
        summary="ticket",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        status_ref=status.ref(),
        type_ref=bug_type.ref(),
        reporter_ref=bob_jira.ref(),
    )

    g.add_project(jira_project)
    g.issue_statuses.add(status)
    g.issue_types.add(bug_type)
    g.jira_users.add(bob_jira)
    g.issues.add(issue)

    return g


def test_dump_and_lazy_roundtrip(tmp_path: Path):
    g = _build_small_graph()

    store = PickleStore(tmp_path / "out")
    g.dump(store)

    # Every typed field is dumped — meta.json lists them all.
    meta = store.meta_read()
    assert meta is not None
    assert meta["project_id"] == "round-trip"
    assert meta["schema_version"] == 2
    # A few representative names must be in the meta listing.
    for name in (
        "commit",
        "file",
        "issue",
        "git_project",
        "jira_project",
        "trait",
        "relation",
    ):
        assert name in meta["registries"]

    # Lazy reload through the proxy.
    g2 = Graph.lazy("round-trip", store)
    assert g2.project_id == "round-trip"
    assert g2.schema_version == 2

    # Each typed field is a proxy that ``isinstance``-matches the
    # original concrete class (see ``lazy_proxy_for`` docstring).
    assert isinstance(g2.commits, LazyRegistryProxy)
    assert isinstance(g2.files, LazyRegistryProxy)
    assert isinstance(g2.issues, LazyRegistryProxy)

    # Resolve round-trip: each entity comes back through resolve().
    commit = g2.resolve(EntityRef(kind=EntityKind.COMMIT, id="abc"))
    assert commit is not None
    assert commit.message == "initial"

    file = g2.resolve(EntityRef(kind=EntityKind.FILE, id="src/app.py"))
    assert file is not None
    assert file.path == "src/app.py"

    issue = g2.resolve(EntityRef(kind=EntityKind.ISSUE, id="JIR-1"))
    assert issue is not None
    assert issue.summary == "ticket"

    # Project resolution walks every project registry — both git and
    # jira sides round-trip.
    git_proj = g2.resolve(EntityRef(kind=EntityKind.PROJECT, id="gp1"))
    jira_proj = g2.resolve(EntityRef(kind=EntityKind.PROJECT, id="jp1"))
    assert git_proj is not None and git_proj.id == "gp1"
    assert jira_proj is not None and jira_proj.id == "jp1"


def test_dump_persists_empty_registries(tmp_path: Path):
    """Even empty registries land on disk so :meth:`Graph.lazy` can
    bind a proxy for every field. Missing pickles fall back to an empty
    instance — both paths are valid.
    """
    g = Graph(project_id="empty-x")
    store = PickleStore(tmp_path / "empty")
    g.dump(store)

    meta = store.meta_read()
    assert meta is not None
    # All 31 typed registries get a file.
    assert len(meta["registries"]) >= 30


def test_lazy_with_missing_pickle_falls_back_to_empty_registry(tmp_path: Path):
    """``Graph.lazy`` against a store with NO pickled file for a given
    name returns a proxy whose loader yields an empty instance. This is
    the greenfield-add path (Chunk 8 added a new registry field that
    older pickle stores didn't write).
    """
    store = PickleStore(tmp_path / "partial")
    store.meta_write({"schema_version": 2, "project_id": "partial"})

    g = Graph.lazy("partial", store)
    # Reading the empty registry — proxy materialises to a fresh
    # instance of the concrete class.
    assert len(g.commits) == 0
    assert len(g.relations) == 0
