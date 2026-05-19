"""Tests for ``commit_issues`` / ``pr_commits`` / ``issue_commits``.

These are the three legacy entity-side navigation helpers reshaped as
free functions (plan §11 rows 4–6, see helpers.py for rationale).

Each test builds the minimum graph needed to exercise one helper:

* ``commit_issues``: 1 issue + 2 commits — only the commit whose
  message mentions the issue key resolves to a non-empty list.
* ``pr_commits``: 1 PR with one commit_ref → GitHubCommit → matching
  git Commit via sha.
* ``issue_commits``: the inverse of commit_issues — scans all commit
  messages for one issue's key.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.common.domains.git.models import Commit, GitAccount, GitProject
from src.common.domains.github.models import (
    GitHubCommit,
    GitHubProject,
    GitHubUser,
    PullRequest,
)
from src.common.domains.jira.models import (
    Issue,
    IssueStatus,
    IssueType,
    JiraProject,
    JiraUser,
)
from src.common.kernel import Graph
from src.common.people import SourceKind
from src.sandbox import (
    MCPSandboxView,
    commit_issues,
    issue_commits,
    pr_commits,
)


# ----------------------------------------------------------------------
# Helpers for the test fixtures
# ----------------------------------------------------------------------
def _utc(year: int = 2024) -> datetime:
    return datetime(year, 1, 1, tzinfo=timezone.utc)


def _add_git(graph: Graph) -> tuple[GitProject, GitAccount]:
    gp = GitProject(id="gp1", name="zep", source=SourceKind.GIT)
    alice = GitAccount(
        id="alice", name="Alice", project_ref=gp.ref(), email="a@x"
    )
    graph.add_project(gp)
    graph.git_accounts.add(alice)
    return gp, alice


def _add_jira(graph: Graph) -> tuple[JiraProject, JiraUser, IssueStatus, IssueType]:
    jp = JiraProject(id="jp1", name="JIR", source=SourceKind.JIRA)
    bob = JiraUser(
        id="bob",
        name="Bob",
        project_ref=jp.ref(),
        key="bob",
        link="https://jira/bob",
    )
    st = IssueStatus(id="open", project_ref=jp.ref(), name="Open", category="new")
    typ = IssueType(id="bug", project_ref=jp.ref(), name="Bug")
    graph.add_project(jp)
    graph.jira_users.add(bob)
    graph.issue_statuses.add(st)
    graph.issue_types.add(typ)
    return jp, bob, st, typ


def _make_commit(
    graph: Graph,
    gp: GitProject,
    alice: GitAccount,
    sha: str,
    message: str,
) -> Commit:
    c = Commit(
        id=sha,
        sha=sha,
        project_ref=gp.ref(),
        message=message,
        author_date=_utc(),
        committer_date=_utc(),
        author_ref=alice.ref(),
        committer_ref=alice.ref(),
    )
    graph.commits.add(c)
    return c


def _make_issue(
    graph: Graph,
    jp: JiraProject,
    st: IssueStatus,
    typ: IssueType,
    bob: JiraUser,
    key: str,
) -> Issue:
    i = Issue(
        id=key,
        project_ref=jp.ref(),
        key=key,
        summary="x",
        created_at=_utc(),
        updated_at=_utc(),
        status_ref=st.ref(),
        type_ref=typ.ref(),
        reporter_ref=bob.ref(),
    )
    graph.issues.add(i)
    return i


# ----------------------------------------------------------------------
# commit_issues
# ----------------------------------------------------------------------
def test_commit_issues_finds_issue_by_key_in_message():
    g = Graph(project_id="ci-1")
    gp, alice = _add_git(g)
    jp, bob, st, typ = _add_jira(g)
    issue = _make_issue(g, jp, st, typ, bob, "JIR-1")

    commit = _make_commit(g, gp, alice, "sha1", "Fix JIR-1: tidy parser")
    result = commit_issues(commit, g)
    assert [i.key for i in result] == ["JIR-1"]


def test_commit_issues_case_insensitive():
    g = Graph(project_id="ci-2")
    gp, alice = _add_git(g)
    jp, bob, st, typ = _add_jira(g)
    _make_issue(g, jp, st, typ, bob, "JIR-1")

    commit = _make_commit(g, gp, alice, "sha2", "Refers to jir-1 here")
    result = commit_issues(commit, g)
    assert [i.key for i in result] == ["JIR-1"]


def test_commit_issues_unrelated_commit_returns_empty():
    g = Graph(project_id="ci-3")
    gp, alice = _add_git(g)
    jp, bob, st, typ = _add_jira(g)
    _make_issue(g, jp, st, typ, bob, "JIR-1")

    commit = _make_commit(g, gp, alice, "sha3", "Random commit, no issue")
    assert commit_issues(commit, g) == []


def test_commit_issues_works_through_mcpsandboxview():
    g = Graph(project_id="ci-4")
    gp, alice = _add_git(g)
    jp, bob, st, typ = _add_jira(g)
    _make_issue(g, jp, st, typ, bob, "JIR-9")

    commit = _make_commit(g, gp, alice, "sha4", "Fix JIR-9 in module X")
    view = MCPSandboxView(g)
    # Same helper, view passed in instead of bare Graph — __getattr__
    # passes the registry calls through.
    result = commit_issues(commit, view)
    assert [i.key for i in result] == ["JIR-9"]


def test_commit_issues_empty_message_returns_empty():
    g = Graph(project_id="ci-5")
    gp, alice = _add_git(g)
    jp, bob, st, typ = _add_jira(g)
    _make_issue(g, jp, st, typ, bob, "JIR-2")
    commit = _make_commit(g, gp, alice, "sha5", "")
    assert commit_issues(commit, g) == []


def test_commit_issues_no_jira_loaded_returns_empty():
    g = Graph(project_id="ci-6")
    gp, alice = _add_git(g)
    commit = _make_commit(g, gp, alice, "sha6", "Fix JIR-1 hopefully")
    # No issues in the graph — helper short-circuits.
    assert commit_issues(commit, g) == []


def test_commit_issues_dedups_repeated_keys():
    g = Graph(project_id="ci-7")
    gp, alice = _add_git(g)
    jp, bob, st, typ = _add_jira(g)
    _make_issue(g, jp, st, typ, bob, "JIR-3")
    commit = _make_commit(
        g, gp, alice, "sha7", "JIR-3 JIR-3 jir-3 mentioned three times"
    )
    result = commit_issues(commit, g)
    assert len(result) == 1
    assert result[0].key == "JIR-3"


# ----------------------------------------------------------------------
# issue_commits
# ----------------------------------------------------------------------
def test_issue_commits_finds_commits_mentioning_issue_key():
    g = Graph(project_id="ic-1")
    gp, alice = _add_git(g)
    jp, bob, st, typ = _add_jira(g)
    issue = _make_issue(g, jp, st, typ, bob, "JIR-10")
    _make_commit(g, gp, alice, "sha1", "Fix JIR-10: parser")
    _make_commit(g, gp, alice, "sha2", "Unrelated cleanup")
    _make_commit(g, gp, alice, "sha3", "JIR-10 second pass")

    result = issue_commits(issue, g)
    assert {c.id for c in result} == {"sha1", "sha3"}


def test_issue_commits_case_insensitive():
    g = Graph(project_id="ic-2")
    gp, alice = _add_git(g)
    jp, bob, st, typ = _add_jira(g)
    issue = _make_issue(g, jp, st, typ, bob, "JIR-11")
    _make_commit(g, gp, alice, "sha1", "ref jir-11 inside")
    result = issue_commits(issue, g)
    assert [c.id for c in result] == ["sha1"]


def test_issue_commits_no_commits_returns_empty():
    g = Graph(project_id="ic-3")
    _, _ = _add_git(g)
    jp, bob, st, typ = _add_jira(g)
    issue = _make_issue(g, jp, st, typ, bob, "JIR-12")
    assert issue_commits(issue, g) == []


def test_issue_commits_through_view():
    g = Graph(project_id="ic-4")
    gp, alice = _add_git(g)
    jp, bob, st, typ = _add_jira(g)
    issue = _make_issue(g, jp, st, typ, bob, "JIR-13")
    _make_commit(g, gp, alice, "sha1", "JIR-13: x")
    view = MCPSandboxView(g)
    result = issue_commits(issue, view)
    assert [c.id for c in result] == ["sha1"]


# ----------------------------------------------------------------------
# pr_commits
# ----------------------------------------------------------------------
@pytest.fixture
def pr_graph() -> tuple[Graph, PullRequest, Commit]:
    g = Graph(project_id="prc")
    gp, alice = _add_git(g)
    # The git Commit's id IS the sha — that's the join key.
    git_commit = _make_commit(g, gp, alice, "shaPR1", "PR-side commit")

    gh = GitHubProject(id="ghp", name="zep-gh", source=SourceKind.GITHUB)
    g.add_project(gh)
    gh_user = GitHubUser(
        id="alice-gh", name="Alice", project_ref=gh.ref(), login="alice-gh"
    )
    g.github_users.add(gh_user)

    pr = PullRequest(
        id="11",
        project_ref=gh.ref(),
        number=11,
        title="t",
        body="b",
        state="closed",
        author_ref=gh_user.ref(),
        created_at=_utc(),
        updated_at=_utc(),
    )
    gh_commit = GitHubCommit(
        id="shaPR1",
        pull_request_ref=pr.ref(),
        sha="shaPR1",
        date=_utc(),
        message="PR-side commit",
        author_ref=gh_user.ref(),
    )
    pr.commit_refs = [gh_commit.ref()]
    g.github_commits.add(gh_commit)
    g.pull_requests.add(pr)

    return g, pr, git_commit


def test_pr_commits_resolves_through_sha_join(pr_graph):
    g, pr, git_commit = pr_graph
    result = pr_commits(pr, g)
    assert [c.id for c in result] == [git_commit.id]


def test_pr_commits_missing_github_commit_skipped(pr_graph):
    g, pr, _ = pr_graph
    # Drop the GitHubCommit — pr.commit_refs[0] now resolves to None.
    g.github_commits.remove("shaPR1")
    assert pr_commits(pr, g) == []


def test_pr_commits_missing_git_side_returns_empty(pr_graph):
    g, pr, _ = pr_graph
    g.commits.remove("shaPR1")
    assert pr_commits(pr, g) == []


def test_pr_commits_empty_commit_refs():
    g = Graph(project_id="prc-2")
    gh = GitHubProject(id="ghp", name="zep-gh", source=SourceKind.GITHUB)
    g.add_project(gh)
    gh_user = GitHubUser(
        id="alice-gh", name="Alice", project_ref=gh.ref(), login="alice-gh"
    )
    g.github_users.add(gh_user)
    pr = PullRequest(
        id="22",
        project_ref=gh.ref(),
        number=22,
        title="t",
        body="b",
        state="open",
        author_ref=gh_user.ref(),
        created_at=_utc(),
        updated_at=_utc(),
    )
    g.pull_requests.add(pr)
    assert pr_commits(pr, g) == []


def test_pr_commits_through_view(pr_graph):
    g, pr, git_commit = pr_graph
    view = MCPSandboxView(g)
    result = pr_commits(pr, view)
    assert [c.id for c in result] == [git_commit.id]
