"""Synthetic graph fixtures for enrichment tests.

We build real Pydantic GitProject / GitCommit / File / Change / Hunk objects
(no mocks) so the taggers exercise the same code paths they would on a loaded
project. Identity-preserving constructors only — the registries reflect the
caller's wiring, not auto-derived links.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

# Importing from `src.common.models` runs the cross-module model_rebuild() so
# forward references inside Pydantic models (GitCommit ↔ GitAccount, etc.) are
# resolved before we instantiate anything.
from src.common.models import (
    Change,
    ChangeType,
    File,
    GitAccount,
    GitAccountId,
    GitCommit,
    GitProject,
    Hunk,
    LineChange,
    LineOperation,
)
from src.common.models import GitHubProject, GitHubUser, PullRequest, GitHubCommit
from src.common.models import (
    Issue,
    IssueStatus,
    IssueStatusCategory,
    IssueType,
    JiraProject,
    JiraUser,
)


UTC = timezone.utc
EPOCH = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


def make_account(name: str, email: str) -> GitAccount:
    return GitAccount(
        git_id=GitAccountId(name=name, email=email),
        commits=[],
    )


def make_hunk(added: int, deleted: int, commit: GitCommit) -> Hunk:
    line_changes = []
    for i in range(added):
        line_changes.append(LineChange(
            operation=LineOperation.ADD, line_number=i + 1, commit=commit,
        ))
    for i in range(deleted):
        line_changes.append(LineChange(
            operation=LineOperation.DELETE, line_number=i + 1, commit=commit,
        ))
    return Hunk(line_changes=line_changes)


def make_change(
    commit: GitCommit,
    file_: File,
    new_name: str,
    *,
    old_name: Optional[str] = None,
    added: int = 5,
    deleted: int = 0,
    change_type: ChangeType = ChangeType.MODIFY,
) -> Change:
    chg = Change(
        commit=commit,
        change_type=change_type,
        old_file_name=old_name or new_name,
        new_file_name=new_name,
        file=file_,
        hunks=[make_hunk(added, deleted, commit)],
    )
    file_.changes.append(chg)
    commit.changes.append(chg)
    return chg


def make_commit(
    project: GitProject,
    cid: str,
    message: str,
    author: GitAccount,
    when: datetime,
) -> GitCommit:
    c = GitCommit(
        project=project,
        id=cid,
        message=message,
        author_date=when,
        committer_date=when,
        author=author,
        committer=author,
    )
    author.commits.append(c)
    return c


def make_file(project: GitProject) -> File:
    f = File(is_binary=False, project=project, id=uuid.uuid4())
    return f


# ── Hand-crafted project ──────────────────────────────────────────────────────
#
# 6 commits, 3 files, 2 authors, anchor = latest commit.
# Designed to trigger:
#   - BugMagnet on src/buggy.py (bugfix-heavy)
#   - BusFactor1 (Hermit) on src/owner.py (one-author dominance)
#   - Orphan on src/orphan.py (single author, last touch outside recent window)
#   - PivotFile only on src/buggy.py if it co-changes with both other files
#
def build_synthetic_graph(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(UTC)
    proj = GitProject(name="synthetic")

    alice = make_account("Alice", "alice@example.com")
    bob = make_account("Bob", "bob@example.com")
    proj.account_registry.add_all([alice, bob])

    buggy = make_file(proj)
    owner = make_file(proj)
    orphan = make_file(proj)
    proj.file_registry.add_all([buggy, owner, orphan])

    # Old orphan touch — well before the recent window (180 days back).
    old = now - timedelta(days=400)
    c_orphan = make_commit(proj, "c_orphan", "initial drop", alice, old)
    make_change(c_orphan, orphan, "src/orphan.py", added=20, change_type=ChangeType.ADD)

    # Owner: only Alice contributes. 3 commits, all in the last 30 days.
    c_o1 = make_commit(proj, "c_o1", "feat: add owner", alice, now - timedelta(days=30))
    make_change(c_o1, owner, "src/owner.py", added=100, change_type=ChangeType.ADD)
    c_o2 = make_commit(proj, "c_o2", "refactor owner", alice, now - timedelta(days=20))
    make_change(c_o2, owner, "src/owner.py", added=50, deleted=10)
    c_o3 = make_commit(proj, "c_o3", "tweak owner", alice, now - timedelta(days=10))
    make_change(c_o3, owner, "src/owner.py", added=20, deleted=5)
    # Add tiny Bob touch so distinct_authors >= 2 (BusFactor1 needs that).
    c_o4 = make_commit(proj, "c_o4", "chore: format owner", bob, now - timedelta(days=5))
    make_change(c_o4, owner, "src/owner.py", added=1, deleted=1)

    # Buggy: many bugfix commits + co-change with the other two.
    bug_commits: list[GitCommit] = []
    for i in range(6):
        msg = "fix: crash on null" if i < 5 else "feat: shiny"
        c = make_commit(
            proj,
            f"c_buggy_{i}",
            msg,
            alice if i % 2 == 0 else bob,
            now - timedelta(days=60 - i * 5),
        )
        make_change(c, buggy, "src/buggy.py", added=10, deleted=2)
        # Co-change one of the other files in some commits to seed PivotFile signal.
        if i % 2 == 0:
            make_change(c, owner, "src/owner.py", added=2, deleted=1)
        # orphan.py intentionally untouched by buggy commits so it keeps the
        # 'single author + last touch outside recent window' shape.
        bug_commits.append(c)

    proj.git_commit_registry.add_all([c_orphan, c_o1, c_o2, c_o3, c_o4, *bug_commits])

    return {"git": proj, "jira": None, "github": None}


# ── Jira fixture ──────────────────────────────────────────────────────────────

def build_jira_fixture(now: Optional[datetime] = None) -> JiraProject:
    """Two issues: one open and old (TasksBottleneck), one done."""
    now = now or datetime.now(UTC)
    proj = JiraProject(name="synthetic-jira")

    open_cat = IssueStatusCategory(key="indeterminate", name="In Progress")
    done_cat = IssueStatusCategory(key="done", name="Done")
    open_status = IssueStatus(id="1", name="In Progress", issue_status_categories=open_cat)
    done_status = IssueStatus(id="2", name="Done", issue_status_categories=done_cat)

    bug_type = IssueType(id="t1", name="Bug", description="bug", isSubTask=False)

    user = JiraUser(key="alice", name="Alice", link="x")

    very_old = Issue(
        id=1,
        key="PROJ-1",
        summary="Old open bug",
        createdAt=now - timedelta(days=400),
        updatedAt=now - timedelta(days=10),
        issue_statuses=[open_status],
        issue_types=[bug_type],
        jira_users_as_assignee=[user],
    )
    fresh = Issue(
        id=2,
        key="PROJ-2",
        summary="Fresh resolved",
        createdAt=now - timedelta(days=5),
        updatedAt=now - timedelta(days=1),
        issue_statuses=[done_status],
        issue_types=[bug_type],
    )
    proj.issue_registry.add_all([very_old, fresh])
    proj.issue_status_registry.add_all([open_status, done_status])
    proj.issue_type_registry.add_all([bug_type])
    proj.issue_status_category_registry.add_all([open_cat, done_cat])
    return proj


def build_github_fixture() -> GitHubProject:
    proj = GitHubProject(name="synthetic-github")
    pr = PullRequest(
        number=7,
        title="Small PR",
        state="merged",
        changedFiles=2,
        body="",
        createdAt=EPOCH,
        mergedAt=EPOCH,
        closedAt=EPOCH,
        updatedAt=EPOCH,
    )
    big = PullRequest(
        number=8,
        title="Massive refactor",
        state="open",
        changedFiles=300,
        body="",
        createdAt=EPOCH,
        mergedAt=None,
        closedAt=None,
        updatedAt=EPOCH,
    )
    proj.pull_request_registry.add_all([pr, big])
    return proj


# ── Cohesion fixture (Bazaar / Cathedral / Pulsar / SharedKnowledge) ─────────
#
# Three files engineered to trigger the cohesion family + SharedKnowledge:
#   - `src/bazaar.py`   — many distinct authors recently
#   - `src/cathedral.py` — one author owns >=4 recent commits exclusively
#   - `src/pulsar.py`   — bursty inter-commit gaps (long quiet, then a spike)
#                          plus several authors with comparable churn so it
#                          also fires SharedKnowledge.
#
def build_cohesion_graph(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(UTC)
    proj = GitProject(name="cohesion-synth")

    authors = [make_account(f"Dev{i}", f"dev{i}@example.com") for i in range(6)]
    proj.account_registry.add_all(authors)

    bazaar = make_file(proj)
    cathedral = make_file(proj)
    pulsar = make_file(proj)
    proj.file_registry.add_all([bazaar, cathedral, pulsar])

    commits: list[GitCommit] = []

    # Bazaar: 6 distinct authors all active in the last 30 days.
    for i, author in enumerate(authors):
        c = make_commit(
            proj,
            f"c_baz_{i}",
            f"chore: bazaar touch {i}",
            author,
            now - timedelta(days=10 + i),
        )
        make_change(c, bazaar, "src/bazaar.py", added=5)
        commits.append(c)

    # Cathedral: 5 recent commits, all by Dev0 (>=80% dominance, >=4 commits).
    for i in range(5):
        c = make_commit(
            proj,
            f"c_cath_{i}",
            f"refactor: cathedral pass {i}",
            authors[0],
            now - timedelta(days=15 + i * 2),
        )
        make_change(c, cathedral, "src/cathedral.py", added=8)
        commits.append(c)

    # Pulsar: cluster of 4 commits within 2 days, then a long gap, then a
    # cluster of 3 — gives high CV and >=6 commits, >=3 intervals. Authors
    # spread across Dev0..Dev3 with comparable churn so SharedKnowledge fires.
    pulsar_offsets = [
        (200, 0), (199, 1), (198, 2), (197, 3),  # cluster 1, days back
        (40, 0),  (39, 1),  (38, 2),             # cluster 2 after a long quiet
    ]
    for i, (days_back, author_idx) in enumerate(pulsar_offsets):
        c = make_commit(
            proj,
            f"c_pulse_{i}",
            f"feat: pulsar burst {i}",
            authors[author_idx],
            now - timedelta(days=days_back),
        )
        make_change(c, pulsar, "src/pulsar.py", added=15, deleted=5)
        commits.append(c)

    proj.git_commit_registry.add_all(commits)

    return {"git": proj, "jira": None, "github": None}


# ── PivotFile fixture ─────────────────────────────────────────────────────────
#
# `src/hub.py` co-changes with 12 distinct other files (each in its own
# 2-file commit so the bulk-commit guard doesn't kick in). 12 > the default
# `pivotfile_cochange_degree_min=10` threshold.
#
def build_pivot_graph(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(UTC)
    proj = GitProject(name="pivot-synth")

    alice = make_account("Alice", "alice@example.com")
    bob = make_account("Bob", "bob@example.com")
    proj.account_registry.add_all([alice, bob])

    hub = make_file(proj)
    spokes = [make_file(proj) for _ in range(12)]
    proj.file_registry.add_all([hub, *spokes])

    commits: list[GitCommit] = []
    for i, spoke in enumerate(spokes):
        c = make_commit(
            proj,
            f"c_pivot_{i}",
            f"feat: wire spoke {i}",
            alice if i % 2 == 0 else bob,
            now - timedelta(days=30 + i),
        )
        make_change(c, hub, "src/hub.py", added=4)
        make_change(c, spoke, f"src/spoke_{i}.py", added=10, change_type=ChangeType.ADD)
        commits.append(c)

    proj.git_commit_registry.add_all(commits)
    return {"git": proj, "jira": None, "github": None}
