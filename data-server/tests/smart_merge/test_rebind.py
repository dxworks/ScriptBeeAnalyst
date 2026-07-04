"""Tests for ``src.smart_merge.rebind`` — the once-only role-ref → UU rewrite.

Task P3.B (UnifiedUsers redesign §D / §E / §M). Builds a 3-source graph
inline (no Zeppelin fixture) covering:

* one pre-existing :class:`UnifiedUser` that already merges Alice's git
  account + github user;
* one orphan git account (Bob — never matched anyone);
* one orphan jira user (Carol — present only in Jira);
* enough domain entities (Commit / PullRequest / Issue) with role-typed
  refs to exercise every spec arity (required-singular, optional-
  singular, plural).

The fixture is laid out so the post-rebind invariants are easy to
verify entity-by-entity in the assertions block.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

# Module-import-time env shims — matches the rest of the smart_merge
# test suite (the server module reads these at import time).
os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake")
os.environ.setdefault("WORKSPACE_ROOT", "/tmp")

import pytest  # noqa: E402

from src.common.domains.git.models import (  # noqa: E402
    Commit,
    GitAccount,
    GitProject,
)
from src.common.domains.github.models import (  # noqa: E402
    GitHubProject,
    GitHubUser,
    PullRequest,
)
from src.common.domains.jira.models import (  # noqa: E402
    Comment,
    Issue,
    IssueStatus,
    IssueTransition,
    IssueType,
    JiraProject,
    JiraUser,
)
from src.common.kernel import (  # noqa: E402
    AccountRoleRegistry,
    EntityKind,
    EntityRef,
    Graph,
    MergeState,
)
from src.common.people import SourceKind  # noqa: E402
from src.common.people.unified import UnifiedUser  # noqa: E402
from src.smart_merge.rebind import (  # noqa: E402
    RebindStats,
    rebind_account_refs_to_unified,
)


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------
ALICE_NAME = "Alice Example"
ALICE_EMAIL = "alice@example.com"
ALICE_LOGIN = "alice-example"

BOB_NAME = "Bob Stranger"
BOB_EMAIL = "bob@example.com"

CAROL_NAME = "Carol Reporter"
CAROL_JIRA_KEY = "carol"


def _build_fixture_graph() -> tuple[Graph, dict]:
    """Build the 3-source graph + return handles to every entity so the
    test body can write assertions without re-fetching by id.
    """
    graph = Graph(project_id="test-rebind")

    git_proj = GitProject(id="gp:demo", name="demo-git", source=SourceKind.GIT)
    graph.add_project(git_proj)
    jira_proj = JiraProject(id="jp:demo", name="demo-jira", source=SourceKind.JIRA)
    graph.add_project(jira_proj)
    github_proj = GitHubProject(
        id="ghp:demo", name="demo-github", source=SourceKind.GITHUB
    )
    graph.add_project(github_proj)

    # --- Accounts --------------------------------------------------------
    # Alice merged across git + github (pre-existing UU).
    alice_uu = UnifiedUser(
        id="uu-alice",
        display_name=ALICE_NAME,
        primary_email=ALICE_EMAIL,
        account_refs=[],  # populated below
    )

    alice_git = GitAccount(
        id=GitAccount.make_id(ALICE_NAME, ALICE_EMAIL),
        name=ALICE_NAME,
        email=ALICE_EMAIL,
        project_ref=git_proj.ref(),
        unified_user_id=alice_uu.id,
    )
    graph.git_accounts.add(alice_git)

    alice_github = GitHubUser(
        id=f"https://github.com/{ALICE_LOGIN}",
        name=ALICE_NAME,
        login=ALICE_LOGIN,
        url=f"https://github.com/{ALICE_LOGIN}",
        project_ref=github_proj.ref(),
        unified_user_id=alice_uu.id,
    )
    graph.github_users.add(alice_github)

    alice_uu.account_refs = [alice_git.ref(), alice_github.ref()]
    graph.unified_users.add(alice_uu)

    # Bob: orphan git account, will get a singleton UU created.
    bob_git = GitAccount(
        id=GitAccount.make_id(BOB_NAME, BOB_EMAIL),
        name=BOB_NAME,
        email=BOB_EMAIL,
        project_ref=git_proj.ref(),
    )
    graph.git_accounts.add(bob_git)

    # Second github user — also points at Alice's UU, used as a separate
    # reviewer/assignee target so the PR list fields exercise plural
    # rewrites with two distinct accounts on the same UU.
    alice_github_bot = GitHubUser(
        id=f"https://github.com/{ALICE_LOGIN}-bot",
        name=f"{ALICE_NAME} (bot)",
        login=f"{ALICE_LOGIN}-bot",
        url=f"https://github.com/{ALICE_LOGIN}-bot",
        project_ref=github_proj.ref(),
        unified_user_id=alice_uu.id,
    )
    graph.github_users.add(alice_github_bot)
    alice_uu.account_refs = [
        alice_git.ref(),
        alice_github.ref(),
        alice_github_bot.ref(),
    ]

    # Carol: orphan jira user, will get a singleton UU created.
    carol_jira = JiraUser(
        id=f"https://jira.example.com/{CAROL_JIRA_KEY}",
        name=CAROL_NAME,
        key=CAROL_JIRA_KEY,
        link=f"https://jira.example.com/{CAROL_JIRA_KEY}",
        project_ref=jira_proj.ref(),
    )
    graph.jira_users.add(carol_jira)

    # --- Domain entities with role refs ---------------------------------
    now = datetime.now(timezone.utc)

    commit_1 = Commit(
        id=Commit.make_id("demo-git", "sha1"),
        project_ref=git_proj.ref(),
        sha="sha1",
        message="first commit",
        author_date=now,
        committer_date=now,
        author_ref=alice_git.ref(),
        committer_ref=alice_git.ref(),
    )
    graph.commits.add(commit_1)

    commit_2 = Commit(
        id=Commit.make_id("demo-git", "sha2"),
        project_ref=git_proj.ref(),
        sha="sha2",
        message="second commit",
        author_date=now,
        committer_date=now,
        author_ref=bob_git.ref(),
        committer_ref=alice_git.ref(),
    )
    graph.commits.add(commit_2)

    pr = PullRequest(
        id=PullRequest.make_id(1),
        project_ref=github_proj.ref(),
        number=1,
        title="PR",
        state="merged",
        created_at=now,
        author_ref=alice_github.ref(),
        merged_by_ref=alice_github.ref(),
        assignee_refs=[alice_github.ref(), alice_github_bot.ref()],
        requested_reviewer_refs=[alice_github_bot.ref()],
    )
    graph.pull_requests.add(pr)

    # Need an IssueStatus / IssueType for the Issue.
    status = IssueStatus(
        id="status:open",
        project_ref=jira_proj.ref(),
        name="Open",
        category="new",
    )
    graph.issue_statuses.add(status)
    itype = IssueType(
        id="type:bug",
        project_ref=jira_proj.ref(),
        name="Bug",
    )
    graph.issue_types.add(itype)

    # Value-object payloads (P3.C):
    # - One IssueTransition with a populated ``user_ref`` (Carol — the
    #   orphan jira user). Exercises the parent-list rewrite path.
    # - One Comment with both ``author_ref`` and ``updated_by_ref``
    #   populated (Carol again). Exercises model_copy on a frozen
    #   value object with two fields rewritten in two passes.
    # - One Comment with ``updated_by_ref=None`` to confirm optional
    #   None values survive the rewrite untouched.
    transition_1 = IssueTransition(
        id=1,
        created=now,
        changed_fields=["status"],
        user_ref=carol_jira.ref(),
    )
    comment_full = Comment(
        body="comment body",
        created=now,
        updated=now,
        author_ref=carol_jira.ref(),
        updated_by_ref=carol_jira.ref(),
    )
    comment_partial = Comment(
        body="never edited",
        created=now,
        updated=now,
        author_ref=carol_jira.ref(),
        updated_by_ref=None,  # remains None after rebind
    )

    issue = Issue(
        id="PROJ-1",
        project_ref=jira_proj.ref(),
        key="PROJ-1",
        summary="bug 1",
        created_at=now,
        updated_at=now,
        status_ref=status.ref(),
        type_ref=itype.ref(),
        creator_ref=carol_jira.ref(),
        reporter_ref=carol_jira.ref(),
        assignee_refs=[carol_jira.ref()],
        comments=[comment_full, comment_partial],
        transitions=[transition_1],
    )
    graph.issues.add(issue)

    handles = {
        "alice_uu": alice_uu,
        "alice_git": alice_git,
        "alice_github": alice_github,
        "alice_github_bot": alice_github_bot,
        "bob_git": bob_git,
        "carol_jira": carol_jira,
        "commit_1": commit_1,
        "commit_2": commit_2,
        "pull_request": pr,
        "issue": issue,
    }
    return graph, handles


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_rebind_rewrites_every_role_ref_and_flips_state() -> None:
    graph, h = _build_fixture_graph()

    # Sanity: pre-rebind, refs target per-source accounts.
    assert graph.merge_state == MergeState.PRE_MERGE
    assert h["commit_1"].author_ref.kind == EntityKind.GIT_ACCOUNT
    assert h["pull_request"].author_ref.kind == EntityKind.GITHUB_USER

    stats = rebind_account_refs_to_unified(graph)

    # --- state ----------------------------------------------------------
    assert graph.merge_state == MergeState.FINALIZED

    # --- stats ----------------------------------------------------------
    # Two singletons: Bob (git) + Carol (jira). Alice's three accounts all
    # carry an existing unified_user_id, so no singletons there.
    assert stats.unified_users_created == 2

    # Refs rewritten breakdown:
    #   commit_1 — author + committer = 2
    #   commit_2 — author + committer = 2
    #   pull_request — author + merged_by + 2 assignees + 1 reviewer = 5
    #   issue — creator + reporter + 1 assignee = 3
    #   transitions (P3.C) — 1 transition.user_ref = 1
    #   comments (P3.C) — comment_full.author + comment_full.updated_by
    #                     + comment_partial.author = 3
    #                     (comment_partial.updated_by_ref is None — skipped)
    # Total = 16.
    assert stats.refs_rewritten == 16
    assert isinstance(stats, RebindStats)

    # --- accounts all carry a unified_user_id ---------------------------
    for acc in list(graph.git_accounts.all()) + list(graph.github_users.all()) + list(
        graph.jira_users.all()
    ):
        assert acc.unified_user_id is not None, (
            f"account {acc.id} has unified_user_id=None after rebind"
        )

    # --- unified_users registry size: 1 pre-existing + 2 singletons -----
    assert len(graph.unified_users.all()) == 3

    # --- every role-typed field flipped to UNIFIED_USER kind ------------
    assert h["commit_1"].author_ref.kind == EntityKind.UNIFIED_USER
    assert h["commit_1"].committer_ref.kind == EntityKind.UNIFIED_USER
    assert h["commit_2"].author_ref.kind == EntityKind.UNIFIED_USER
    assert h["commit_2"].committer_ref.kind == EntityKind.UNIFIED_USER

    assert h["pull_request"].author_ref.kind == EntityKind.UNIFIED_USER
    assert h["pull_request"].merged_by_ref.kind == EntityKind.UNIFIED_USER
    for ref in h["pull_request"].assignee_refs:
        assert ref.kind == EntityKind.UNIFIED_USER
    for ref in h["pull_request"].requested_reviewer_refs:
        assert ref.kind == EntityKind.UNIFIED_USER

    assert h["issue"].creator_ref.kind == EntityKind.UNIFIED_USER
    assert h["issue"].reporter_ref.kind == EntityKind.UNIFIED_USER
    for ref in h["issue"].assignee_refs:
        assert ref.kind == EntityKind.UNIFIED_USER

    # --- correctness of the new ref ids ---------------------------------
    # Alice's git + github accounts all merged into the pre-existing UU.
    alice_uu_id = h["alice_uu"].id
    assert h["commit_1"].author_ref.id == alice_uu_id
    assert h["pull_request"].author_ref.id == alice_uu_id
    # PR.assignee_refs = [alice_github, alice_github_bot] — both Alice.
    assert all(r.id == alice_uu_id for r in h["pull_request"].assignee_refs)

    # Bob's commit_2.author_ref now points at Bob's singleton UU.
    bob_uu_id = h["bob_git"].unified_user_id
    assert bob_uu_id is not None
    assert h["commit_2"].author_ref.id == bob_uu_id

    # Carol's issue refs now point at her singleton UU.
    carol_uu_id = h["carol_jira"].unified_user_id
    assert carol_uu_id is not None
    assert h["issue"].creator_ref.id == carol_uu_id
    assert h["issue"].reporter_ref.id == carol_uu_id
    assert h["issue"].assignee_refs[0].id == carol_uu_id


def test_rebind_on_finalized_graph_raises() -> None:
    """Running the rebind a second time must raise — finalize is one-way."""
    graph, _ = _build_fixture_graph()
    rebind_account_refs_to_unified(graph)
    assert graph.merge_state == MergeState.FINALIZED

    with pytest.raises(ValueError, match="rebind already applied"):
        rebind_account_refs_to_unified(graph)


def test_rebind_indexes_are_keyed_on_unified_user_refs() -> None:
    """After the rebind, the registry indexes (``by_author`` / ``by_reporter``
    / ...) must be keyed on UU refs so ``uu.commits_as_author(g)`` works.
    """
    graph, h = _build_fixture_graph()
    rebind_account_refs_to_unified(graph)

    alice_uu_ref = EntityRef(
        kind=EntityKind.UNIFIED_USER, id=h["alice_uu"].id
    )
    # Alice authored commit_1 and was the committer of both commits.
    by_author_alice = graph.commits.by_author[alice_uu_ref]
    assert h["commit_1"] in by_author_alice
    by_committer_alice = graph.commits.by_committer[alice_uu_ref]
    assert h["commit_1"] in by_committer_alice
    assert h["commit_2"] in by_committer_alice

    bob_uu_ref = EntityRef(
        kind=EntityKind.UNIFIED_USER, id=h["bob_git"].unified_user_id
    )
    by_author_bob = graph.commits.by_author[bob_uu_ref]
    assert h["commit_2"] in by_author_bob

    carol_uu_ref = EntityRef(
        kind=EntityKind.UNIFIED_USER, id=h["carol_jira"].unified_user_id
    )
    by_reporter_carol = graph.issues.by_reporter[carol_uu_ref]
    assert h["issue"] in by_reporter_carol


# ---------------------------------------------------------------------------
# P3.C — nested value-object specs (IssueTransition / Comment)
# ---------------------------------------------------------------------------
_PRODUCTION_OWNING_CLASS_NAMES = {
    # Entity-side specs registered automatically by
    # ``Entity.__pydantic_init_subclass__`` when the domain models load.
    "Commit",
    "PullRequest",
    "Review",
    "ReviewComment",
    "GitHubCommit",
    "Issue",
    # Value-object specs registered explicitly via
    # ``register_value_object_role_refs`` (P3.C).
    "IssueTransition",
    "Comment",
}


def test_account_role_registry_includes_value_object_specs() -> None:
    """After domain-model import, the registry holds 12 Entity-side specs
    plus the 3 new value-object specs (``IssueTransition.user_ref``,
    ``Comment.author_ref``, ``Comment.updated_by_ref``) — 15 production
    specs total.

    Filters out any synthetic ``Entity`` subclasses other tests may
    register (the inline classes in ``test_role_ref.py`` survive across
    test collection because the registry is process-wide).
    """
    production_specs = [
        s
        for s in AccountRoleRegistry.all()
        if s.owning_cls.__name__ in _PRODUCTION_OWNING_CLASS_NAMES
    ]
    assert len(production_specs) == 15, (
        f"expected 12 Entity-side + 3 value-object specs = 15, got "
        f"{len(production_specs)}: "
        f"{[(s.owning_cls.__name__, s.field_name) for s in production_specs]}"
    )
    value_object_specs = [s for s in production_specs if s.value_object]
    assert len(value_object_specs) == 3
    by_key = {
        (s.owning_cls.__name__, s.field_name): s for s in value_object_specs
    }
    assert ("IssueTransition", "user_ref") in by_key
    assert by_key[("IssueTransition", "user_ref")].role == "transitioner"
    assert by_key[("IssueTransition", "user_ref")].optional is True
    assert ("Comment", "author_ref") in by_key
    assert by_key[("Comment", "author_ref")].role == "author"
    assert ("Comment", "updated_by_ref") in by_key
    assert by_key[("Comment", "updated_by_ref")].role == "updated_by"


def test_rebind_rewrites_issue_transition_user_ref() -> None:
    """Each :class:`IssueTransition` inside ``issue.transitions`` must
    have its ``user_ref`` flipped to a ``UNIFIED_USER``-kind ref after
    rebind.
    """
    graph, h = _build_fixture_graph()
    rebind_account_refs_to_unified(graph)

    issue = h["issue"]
    assert len(issue.transitions) == 1
    transition = issue.transitions[0]
    assert transition.user_ref is not None
    assert transition.user_ref.kind == EntityKind.UNIFIED_USER
    # And it points at Carol's singleton UU.
    carol_uu_id = h["carol_jira"].unified_user_id
    assert transition.user_ref.id == carol_uu_id


def test_rebind_rewrites_comment_author_and_updated_by() -> None:
    """Each :class:`Comment` must have both ``author_ref`` and
    ``updated_by_ref`` rewritten when populated. Frozen value objects
    are replaced via ``model_copy(update=...)`` and re-set on the
    parent's ``comments`` list.
    """
    graph, h = _build_fixture_graph()
    rebind_account_refs_to_unified(graph)

    issue = h["issue"]
    carol_uu_id = h["carol_jira"].unified_user_id
    assert len(issue.comments) == 2
    full_comment = issue.comments[0]
    assert full_comment.author_ref is not None
    assert full_comment.author_ref.kind == EntityKind.UNIFIED_USER
    assert full_comment.author_ref.id == carol_uu_id
    assert full_comment.updated_by_ref is not None
    assert full_comment.updated_by_ref.kind == EntityKind.UNIFIED_USER
    assert full_comment.updated_by_ref.id == carol_uu_id


def test_rebind_preserves_none_on_optional_value_object_refs() -> None:
    """A :class:`Comment` whose ``updated_by_ref`` is ``None`` keeps that
    ``None`` through the rebind — optional value-object refs are
    tolerated identically to the Entity-side optional refs.
    """
    graph, h = _build_fixture_graph()

    # Sanity: the partial comment carried updated_by_ref=None pre-rebind.
    issue = h["issue"]
    partial_comment_pre = issue.comments[1]
    assert partial_comment_pre.updated_by_ref is None

    rebind_account_refs_to_unified(graph)

    issue = h["issue"]
    partial_comment = issue.comments[1]
    # author_ref still got rewritten (it was populated).
    assert partial_comment.author_ref is not None
    assert partial_comment.author_ref.kind == EntityKind.UNIFIED_USER
    # updated_by_ref stayed None.
    assert partial_comment.updated_by_ref is None


def test_rebind_does_not_install_value_object_reverse_resolvers() -> None:
    """Value-object specs must NOT produce ``UnifiedUser.<plural>_as_<role>``
    methods — they're queryable via parent-Entity traversal
    (``issue.transitions`` / ``issue.comments``), not via UU reverse
    resolvers. Guards the constraint from P3.C.
    """
    from src.common.people.unified import UnifiedUser

    # The naming convention for IssueTransition/Comment would have been
    # something like ``issue_transitions_as_transitioner``,
    # ``comments_as_author`` — none of these may exist on UnifiedUser.
    forbidden_names = {
        "issue_transitions_as_transitioner",
        "issuetransitions_as_transitioner",
        "transitions_as_transitioner",
        "comments_as_author",
        "comments_as_updated_by",
    }
    for name in forbidden_names:
        assert not hasattr(UnifiedUser, name), (
            f"UnifiedUser.{name} must NOT be auto-installed for "
            f"value-object specs (see P3.C constraint)."
        )
