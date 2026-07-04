"""Auto-installed reverse resolvers on :class:`UnifiedUser` (task P3.A).

See ``unified_users_change.md`` §C — at module-load time the
``unified.py`` module walks :data:`AccountRoleRegistry` and installs a
``<entity-plural>_as_<role>`` method per spec, plus three hand-installed
raw-provenance accessors (``git_accounts`` / ``jira_users`` /
``github_users``) that thin-wrap :meth:`UnifiedUser.accounts_of_kind`.

These tests construct the *post-rebind* state manually — the rebind
pass (task P3.B) doesn't exist yet, so we build graphs where
``Commit.author_ref.kind == UNIFIED_USER`` by hand. That matches the
invariant the reverse resolvers query against.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.common.kernel import (
    EntityKind,
    EntityRef,
    Graph,
)
from src.common.people.unified import (
    UnifiedUser,
    UnifiedUserRegistry,
    _install_reverse_resolvers,
)
from src.common.domains.git.models import Commit, GitAccount, GitProject
from src.common.domains.git.registries import (
    CommitRegistry,
    GitAccountRegistry,
    GitProjectRegistry,
)
from src.common.domains.jira.models import Issue, IssueStatus, IssueType, JiraProject
from src.common.domains.jira.registries import (
    IssueRegistry,
    IssueStatusRegistry,
    IssueTypeRegistry,
    JiraProjectRegistry,
)
from src.common.people import SourceKind


# Importing the domain models above guarantees ``AccountRoleRegistry``
# is populated by the time these tests run. The kernel package init
# also runs ``_install_reverse_resolvers`` once, but we belt-and-braces
# call it here too so the test file is robust to import-order changes.
_install_reverse_resolvers()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _project_ref() -> EntityRef:
    return EntityRef(kind=EntityKind.PROJECT, id="proj-1")


def _git_project() -> GitProject:
    return GitProject(id="proj-1", name="repo", source=SourceKind.GIT)


def _jira_project() -> JiraProject:
    return JiraProject(id="proj-jira", name="ZEPP", source=SourceKind.JIRA)


def _make_uu(id_: str = "uu-1", display: str = "Alice") -> UnifiedUser:
    return UnifiedUser(id=id_, display_name=display, primary_email="alice@example.com")


def _graph_with_commit_uu_authored(uu: UnifiedUser) -> Graph:
    """Build a graph with one commit whose ``author_ref`` already targets
    a UnifiedUser (post-rebind invariant). Includes a GitAccount linked
    to the same UnifiedUser via ``account_refs`` so the raw-provenance
    accessor has something to return.
    """
    project = _git_project()
    git_account = GitAccount(
        id="alice-git",
        name="Alice",
        project_ref=project.ref(),
        email="alice@example.com",
        unified_user_id=uu.id,
    )
    # Glue the git account onto the UU so git_accounts(g) finds it.
    uu = uu.model_copy(update={"account_refs": [git_account.ref()]})

    commit = Commit(
        id="repo:abc123",
        sha="abc123",
        project_ref=project.ref(),
        message="bump",
        author_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        committer_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        # Post-rebind shape: ref kind is UNIFIED_USER.
        author_ref=EntityRef(kind=EntityKind.UNIFIED_USER, id=uu.id),
        committer_ref=EntityRef(kind=EntityKind.UNIFIED_USER, id=uu.id),
    )

    git_projects = GitProjectRegistry()
    git_projects.add(project)
    git_accounts = GitAccountRegistry()
    git_accounts.add(git_account)
    commits = CommitRegistry()
    commits.add(commit)
    uu_reg = UnifiedUserRegistry()
    uu_reg.add(uu)

    return Graph(
        project_id="proj-1",
        git_projects=git_projects,
        git_accounts=git_accounts,
        commits=commits,
        unified_users=uu_reg,
    )


def _graph_with_issue_uu_assigned(uu: UnifiedUser) -> Graph:
    """Build a graph with one Issue whose ``assignee_refs`` contains a
    UnifiedUser ref (post-rebind invariant, plural role).
    """
    project = _jira_project()
    status = IssueStatus(
        id="open",
        project_ref=project.ref(),
        name="Open",
        category="indeterminate",
    )
    type_ = IssueType(id="bug", project_ref=project.ref(), name="Bug")
    issue = Issue(
        id="ZEPP-1",
        project_ref=project.ref(),
        key="ZEPP-1",
        summary="Something broke",
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        status_ref=status.ref(),
        type_ref=type_.ref(),
        assignee_refs=[EntityRef(kind=EntityKind.UNIFIED_USER, id=uu.id)],
    )

    jira_projects = JiraProjectRegistry()
    jira_projects.add(project)
    statuses = IssueStatusRegistry()
    statuses.add(status)
    types = IssueTypeRegistry()
    types.add(type_)
    issues = IssueRegistry()
    issues.add(issue)
    uu_reg = UnifiedUserRegistry()
    uu_reg.add(uu)

    return Graph(
        project_id="proj-jira",
        jira_projects=jira_projects,
        issue_statuses=statuses,
        issue_types=types,
        issues=issues,
        unified_users=uu_reg,
    )


# ---------------------------------------------------------------------------
# 1) commits_as_author exists, is callable, returns a list[Commit]
# ---------------------------------------------------------------------------


def test_commits_as_author_exists_and_returns_commits():
    uu = _make_uu()
    graph = _graph_with_commit_uu_authored(uu)
    # The graph copy mutated ``account_refs``; pull the live entity back.
    uu = graph.unified_users.get(uu.id)

    assert hasattr(UnifiedUser, "commits_as_author")
    method = getattr(uu, "commits_as_author")
    assert callable(method)

    commits = uu.commits_as_author(graph)
    assert isinstance(commits, list)
    assert len(commits) == 1
    assert commits[0].id == "repo:abc123"
    assert commits[0].sha == "abc123"


def test_commits_as_committer_also_installed():
    """Same shape, different role on the same registry — sanity check that
    both Commit role-refs map to distinct resolvers."""
    uu = _make_uu()
    graph = _graph_with_commit_uu_authored(uu)
    uu = graph.unified_users.get(uu.id)

    commits = uu.commits_as_committer(graph)
    assert [c.id for c in commits] == ["repo:abc123"]


# ---------------------------------------------------------------------------
# 2) issues_as_assignee (plural role) returns a list[Issue]
# ---------------------------------------------------------------------------


def test_issues_as_assignee_plural_role():
    uu = _make_uu(id_="uu-bob", display="Bob")
    graph = _graph_with_issue_uu_assigned(uu)

    assert hasattr(UnifiedUser, "issues_as_assignee")
    issues = uu.issues_as_assignee(graph)
    assert isinstance(issues, list)
    assert [i.key for i in issues] == ["ZEPP-1"]


# ---------------------------------------------------------------------------
# 3) git_accounts(g) returns a list of GitAccount entities
# ---------------------------------------------------------------------------


def test_git_accounts_returns_git_account_entities():
    uu = _make_uu()
    graph = _graph_with_commit_uu_authored(uu)
    uu = graph.unified_users.get(uu.id)

    assert hasattr(UnifiedUser, "git_accounts")
    accounts = uu.git_accounts(graph)
    assert isinstance(accounts, list)
    assert len(accounts) == 1
    assert isinstance(accounts[0], GitAccount)
    assert accounts[0].email == "alice@example.com"


def test_jira_users_and_github_users_installed():
    """Sanity: the other two raw-provenance accessors are present and
    return empty lists for a UU with no jira/github accounts attached."""
    uu = _make_uu()
    graph = _graph_with_commit_uu_authored(uu)
    uu = graph.unified_users.get(uu.id)

    assert hasattr(UnifiedUser, "jira_users")
    assert hasattr(UnifiedUser, "github_users")
    assert uu.jira_users(graph) == []
    assert uu.github_users(graph) == []


# ---------------------------------------------------------------------------
# 4) Collision check fires for an unmarked hand-defined method
# ---------------------------------------------------------------------------


def test_collision_check_raises_for_unmarked_clash():
    """A consumer that hand-defines a method with the same name as a
    reverse resolver — without the ``_generated_reverse_resolver``
    marker — should make the installer raise ``TypeError``. We exercise
    the installer directly by stamping an unmarked attribute first.
    """
    from src.common.people.unified import _set_resolver, _make_reverse_resolver

    # Hand-install an unmarked attribute on UnifiedUser. Use a name that
    # WILL clash with a real reverse resolver (commits_as_author).
    sentinel = lambda self, graph: "hand-written"  # noqa: E731
    # Must NOT carry the marker.
    assert not getattr(sentinel, "_generated_reverse_resolver", False)
    UnifiedUser.commits_as_author = sentinel  # type: ignore[assignment]

    try:
        with pytest.raises(TypeError, match="commits_as_author"):
            _set_resolver(
                "commits_as_author",
                _make_reverse_resolver("commits", "author"),
            )
    finally:
        # Reinstall the real resolver so other tests keep working.
        del UnifiedUser.commits_as_author  # type: ignore[attr-defined]
        _install_reverse_resolvers()


def test_idempotent_reinstall_replaces_marked_method():
    """Re-running ``_install_reverse_resolvers`` is safe; a previously-
    marked auto-generated method is replaced silently."""
    first = UnifiedUser.__dict__["commits_as_author"]
    _install_reverse_resolvers()
    second = UnifiedUser.__dict__["commits_as_author"]
    # New function object each install (closure captures the same args),
    # but the marker survives and the name is stable.
    assert getattr(second, "_generated_reverse_resolver", False) is True
    assert first is not second
