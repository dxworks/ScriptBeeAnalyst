"""GitHub-domain registry tests.

Covers every registry's CRUD + declared indexes + pickle round-trip.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.common.domains.github import (
    GitHubCommit,
    GitHubCommitRegistry,
    GitHubProject,
    GitHubProjectRegistry,
    GitHubUser,
    GitHubUserRegistry,
    PullRequest,
    PullRequestRegistry,
    Review,
    ReviewComment,
    ReviewCommentRegistry,
    ReviewRegistry,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind
from src.common.pickle_store import PickleStore


PROJECT_ID = "github-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _user(
    name: str,
    *,
    login: str | None,
    url: str | None = None,
    uu: str | None = None,
) -> GitHubUser:
    eid = url or f"u/{name}"
    return GitHubUser(
        id=eid,
        name=name,
        project_ref=PROJECT_REF,
        login=login,
        url=url,
        unified_user_id=uu,
    )


def _pr(
    number: int,
    state: str = "open",
    *,
    author_ref: EntityRef | None = None,
    project_ref: EntityRef = PROJECT_REF,
) -> PullRequest:
    return PullRequest(
        id=PullRequest.make_id(number),
        project_ref=project_ref,
        number=number,
        title=f"PR #{number}",
        body="",
        state=state,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_ref=author_ref,
    )


def _review(pr_number: int, ordinal: int, state: str, *, author_ref: EntityRef | None) -> Review:
    return Review(
        id=Review.make_id(pr_number, ordinal),
        pull_request_ref=EntityRef(kind=EntityKind.PULL_REQUEST, id=str(pr_number)),
        ordinal=ordinal,
        state=state,
        body="",
        submitted_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_ref=author_ref,
    )


def _review_comment(
    url: str,
    pr_number: int,
    review_id: str,
    *,
    author_ref: EntityRef | None = None,
    file_path: str | None = None,
) -> ReviewComment:
    return ReviewComment(
        id=url,
        review_ref=EntityRef(kind=EntityKind.REVIEW, id=review_id),
        pull_request_ref=EntityRef(kind=EntityKind.PULL_REQUEST, id=str(pr_number)),
        url=url,
        body="body",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_ref=author_ref,
        file_path=file_path,
    )


def _commit(
    sha: str, pr_number: int, *, author_ref: EntityRef | None = None
) -> GitHubCommit:
    return GitHubCommit(
        id=sha,
        pull_request_ref=EntityRef(kind=EntityKind.PULL_REQUEST, id=str(pr_number)),
        sha=sha,
        date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        message=f"commit {sha}",
        author_ref=author_ref,
    )


# ---------------------------------------------------------------------------
# GitHubProjectRegistry
# ---------------------------------------------------------------------------


def test_github_project_registry_add_and_by_name():
    reg = GitHubProjectRegistry()
    p1 = GitHubProject(id="g1", name="zep/zep", source=SourceKind.GITHUB)
    p2 = GitHubProject(id="g2", name="zep/zep", source=SourceKind.GITHUB)
    p3 = GitHubProject(id="g3", name="other", source=SourceKind.GITHUB)
    reg.add(p1)
    reg.add(p2)
    reg.add(p3)
    assert len(reg) == 3
    assert reg.get("g1") is p1
    assert {p.id for p in reg.by_name["zep/zep"]} == {"g1", "g2"}


# ---------------------------------------------------------------------------
# GitHubUserRegistry
# ---------------------------------------------------------------------------


def test_github_user_registry_indexes():
    reg = GitHubUserRegistry()
    alice = _user(
        "Alice", login="alice", url="https://github.com/alice", uu="uu-1"
    )
    bob = _user("Bob", login="bob", url="https://github.com/bob")
    # Repo-owner placeholder record with no login — exercises the None skip.
    owner = _user(
        "Owner", login=None, url="https://github.com/owner"
    )
    other_proj_ref = EntityRef(kind=EntityKind.PROJECT, id="github-2")
    carol = GitHubUser(
        id="https://github.com/carol",
        name="Carol",
        project_ref=other_proj_ref,
        login="carol",
        unified_user_id="uu-1",
    )
    reg.add(alice)
    reg.add(bob)
    reg.add(owner)
    reg.add(carol)

    assert {u.id for u in reg.by_login["alice"]} == {alice.id}
    assert {u.id for u in reg.by_login["bob"]} == {bob.id}
    # The login-less owner record is skipped from the index.
    assert reg.by_login[None] == ()
    # by_project
    assert {u.id for u in reg.by_project[PROJECT_REF]} == {
        alice.id,
        bob.id,
        owner.id,
    }
    assert {u.id for u in reg.by_project[other_proj_ref]} == {carol.id}
    # by_unified_user
    assert {u.id for u in reg.by_unified_user["uu-1"]} == {alice.id, carol.id}
    assert reg.by_unified_user[None] == ()


def test_github_user_registry_remove_updates_indexes():
    reg = GitHubUserRegistry()
    u = _user("X", login="x", url="u/x", uu="uu-1")
    reg.add(u)
    assert reg.by_login["x"] == (u,)
    reg.remove(u.id)
    assert reg.by_login["x"] == ()
    assert reg.by_unified_user["uu-1"] == ()
    assert reg.by_project[PROJECT_REF] == ()


# ---------------------------------------------------------------------------
# PullRequestRegistry
# ---------------------------------------------------------------------------


def test_pull_request_registry_indexes():
    reg = PullRequestRegistry()
    alice = _user("Alice", login="alice", url="u/alice")
    bob = _user("Bob", login="bob", url="u/bob")
    pr1 = _pr(1, state="open", author_ref=alice.ref())
    pr2 = _pr(2, state="merged", author_ref=alice.ref())
    pr3 = _pr(3, state="closed", author_ref=bob.ref())
    pr_anon = _pr(4, state="open")  # no author_ref
    reg.add(pr1)
    reg.add(pr2)
    reg.add(pr3)
    reg.add(pr_anon)

    assert {p.id for p in reg.by_project[PROJECT_REF]} == {"1", "2", "3", "4"}
    assert {p.id for p in reg.by_author[alice.ref()]} == {"1", "2"}
    assert {p.id for p in reg.by_author[bob.ref()]} == {"3"}
    # Anonymous PRs are skipped from by_author (None key).
    assert reg.by_author[None] == ()
    assert {p.id for p in reg.by_state["open"]} == {"1", "4"}
    assert {p.id for p in reg.by_state["merged"]} == {"2"}
    # by_number is indexed by the integer number.
    assert {p.id for p in reg.by_number[1]} == {"1"}
    assert {p.id for p in reg.by_number[4]} == {"4"}


# ---------------------------------------------------------------------------
# ReviewRegistry
# ---------------------------------------------------------------------------


def test_review_registry_indexes():
    reg = ReviewRegistry()
    alice = _user("Alice", login="alice", url="u/alice")
    bob = _user("Bob", login="bob", url="u/bob")
    r1 = _review(1, 0, "APPROVED", author_ref=alice.ref())
    r2 = _review(1, 1, "CHANGES_REQUESTED", author_ref=bob.ref())
    r3 = _review(2, 0, "APPROVED", author_ref=alice.ref())
    reg.add(r1)
    reg.add(r2)
    reg.add(r3)

    pr1_ref = EntityRef(kind=EntityKind.PULL_REQUEST, id="1")
    pr2_ref = EntityRef(kind=EntityKind.PULL_REQUEST, id="2")
    assert {r.id for r in reg.by_pull_request[pr1_ref]} == {"1#0", "1#1"}
    assert {r.id for r in reg.by_pull_request[pr2_ref]} == {"2#0"}
    assert {r.id for r in reg.by_author[alice.ref()]} == {"1#0", "2#0"}
    assert {r.id for r in reg.by_state["APPROVED"]} == {"1#0", "2#0"}
    assert {r.id for r in reg.by_state["CHANGES_REQUESTED"]} == {"1#1"}


# ---------------------------------------------------------------------------
# ReviewCommentRegistry
# ---------------------------------------------------------------------------


def test_review_comment_registry_indexes():
    reg = ReviewCommentRegistry()
    alice = _user("Alice", login="alice", url="u/alice")
    rc1 = _review_comment(
        "u/c1", 1, "1#0", author_ref=alice.ref(), file_path="src/app.py"
    )
    rc2 = _review_comment(
        "u/c2", 1, "1#0", author_ref=alice.ref(), file_path="src/util.py"
    )
    rc3 = _review_comment("u/c3", 2, "2#0", file_path="src/app.py")
    reg.add(rc1)
    reg.add(rc2)
    reg.add(rc3)

    review_1_0 = EntityRef(kind=EntityKind.REVIEW, id="1#0")
    pr1_ref = EntityRef(kind=EntityKind.PULL_REQUEST, id="1")
    assert {c.id for c in reg.by_review[review_1_0]} == {"u/c1", "u/c2"}
    assert {c.id for c in reg.by_pull_request[pr1_ref]} == {"u/c1", "u/c2"}
    assert {c.id for c in reg.by_author[alice.ref()]} == {"u/c1", "u/c2"}
    # rc3 had no author_ref — skipped from by_author.
    assert reg.by_author[None] == ()
    assert {c.id for c in reg.by_file["src/app.py"]} == {"u/c1", "u/c3"}
    assert {c.id for c in reg.by_file["src/util.py"]} == {"u/c2"}


# ---------------------------------------------------------------------------
# GitHubCommitRegistry
# ---------------------------------------------------------------------------


def test_github_commit_registry_indexes():
    reg = GitHubCommitRegistry()
    alice = _user("Alice", login="alice", url="u/alice")
    c1 = _commit("sha1", 1, author_ref=alice.ref())
    c2 = _commit("sha2", 1, author_ref=alice.ref())
    c3 = _commit("sha3", 2)
    reg.add(c1)
    reg.add(c2)
    reg.add(c3)

    pr1_ref = EntityRef(kind=EntityKind.PULL_REQUEST, id="1")
    assert {c.id for c in reg.by_pull_request[pr1_ref]} == {"sha1", "sha2"}
    assert {c.id for c in reg.by_author[alice.ref()]} == {"sha1", "sha2"}
    # by_sha is keyed by string SHA (used by Chunk 7's git↔github linker).
    assert {c.id for c in reg.by_sha["sha1"]} == {"sha1"}
    assert {c.id for c in reg.by_sha["sha2"]} == {"sha2"}


# ---------------------------------------------------------------------------
# Pickle round-trip via PickleStore
# ---------------------------------------------------------------------------


def test_pull_request_registry_pickle_round_trip(tmp_path: Path):
    reg = PullRequestRegistry()
    alice = _user("Alice", login="alice", url="u/alice")
    reg.add(_pr(1, state="open", author_ref=alice.ref()))
    reg.add(_pr(2, state="merged", author_ref=alice.ref()))
    store = PickleStore(tmp_path)
    store.write_registry(EntityKind.PULL_REQUEST.value, reg)
    restored = store.read_registry(
        EntityKind.PULL_REQUEST.value, PullRequestRegistry
    )
    assert len(restored) == 2
    assert {p.id for p in restored.by_state["open"]} == {"1"}
    assert {p.id for p in restored.by_author[alice.ref()]} == {"1", "2"}
    # by_number rebuilt with int keys:
    assert {p.id for p in restored.by_number[1]} == {"1"}


def test_github_user_registry_pickle_round_trip(tmp_path: Path):
    reg = GitHubUserRegistry()
    reg.add(_user("A", login="alice", url="u/alice", uu="uu-1"))
    reg.add(_user("Owner", login=None, url="u/owner"))
    store = PickleStore(tmp_path)
    store.write_registry(EntityKind.GITHUB_USER.value, reg)
    restored = store.read_registry(
        EntityKind.GITHUB_USER.value, GitHubUserRegistry
    )
    assert len(restored) == 2
    assert {u.login for u in restored.by_unified_user["uu-1"]} == {"alice"}
    assert restored.by_login[None] == ()
