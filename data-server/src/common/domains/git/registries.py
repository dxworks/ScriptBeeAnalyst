"""Registries for every Git-domain :class:`Entity` subclass.

Each registry declares the secondary indexes Chunk 7 (relation builders) and
the MCP sandbox helpers (Chunk 11) actually use. Per plan §1.5, indexes are
declared as a ``ClassVar[list[IndexSpec]]`` and rebuilt on every mutation /
on ``Registry.load`` — they are NOT pickled.

There is intentionally no ``GitChangeRegistry``-style domain prefix on these
names. The new ``Graph`` (Chunk 8) groups registries by entity kind; the
``Commit`` / ``File`` / ``Change`` / ``Hunk`` names are unambiguous at the
graph level (Jira's transition entity is namespaced ``IssueTransition`` in
Chunk 5, exactly as plan §4 directs).
"""
from __future__ import annotations

from typing import Optional

from ...kernel import IndexSpec, Registry
from .models import (
    Change,
    Commit,
    File,
    GitAccount,
    GitProject,
    Hunk,
)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class GitProjectRegistry(Registry[GitProject, str]):
    """Holds every :class:`GitProject` in the graph.

    Tiny by design — most graphs have a single git project. The ``by_name``
    index is there to support the smart-merge UI's "give me the project I
    just uploaded" query without scanning all-projects.

    Note: at the graph level, all :class:`Project` subclasses share a single
    :class:`ProjectRegistry` (plan §3 + Chunk 2 design choice §5). This
    domain-specific registry exists so transformer authors can hand the
    processor a strictly-typed `Registry[GitProject, str]` bucket — Chunk
    8 decides whether to merge it into the shared ProjectRegistry or keep
    a per-domain registry. Both options are open with this surface.
    """

    indexes = [
        IndexSpec(name="by_name", key_fn=lambda p: p.name, multi=True),
    ]

    def get_id(self, entity: GitProject) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


def _account_unified_key(a: GitAccount) -> Optional[str]:
    """Key function for the ``by_unified_user`` index.

    Returning ``None`` skips the entity for this spec (per kernel
    ``_normalize_keys`` semantics) — so an account without an accepted
    smart-merge merely never lands in this bucket. Verified by the
    ``test_by_unified_user_skips_none`` test.
    """
    return a.unified_user_id


class GitAccountRegistry(Registry[GitAccount, str]):
    """All :class:`GitAccount` instances seen across the graph.

    Index choices (plan §4.1):

    * ``by_email``        — lookup by the account's email (frequent in
                            smart-merge + tag generation).
    * ``by_project``      — every account that signed a commit in this
                            project (one bucket per ``project_ref``).
    * ``by_unified_user`` — reverse index for "show me every account
                            already merged into this :class:`UnifiedUser`".
                            ``None`` keys are skipped automatically.
    """

    indexes = [
        IndexSpec(name="by_email", key_fn=lambda a: a.email, multi=True),
        IndexSpec(name="by_project", key_fn=lambda a: a.project_ref, multi=True),
        IndexSpec(name="by_unified_user", key_fn=_account_unified_key, multi=True),
    ]

    def get_id(self, entity: GitAccount) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Commits
# ---------------------------------------------------------------------------


class CommitRegistry(Registry[Commit, str]):
    """Every :class:`Commit` seen across the graph.

    Index choices (plan §1.5 + §4.1):

    * ``by_author``    — single :class:`EntityRef` per commit.
    * ``by_committer`` — same, for the committer (often == author).
    * ``by_project``   — one bucket per :class:`GitProject` ref.
    * ``by_parent``    — fan-out over :pyattr:`Commit.parent_refs`. Each
                         parent gets a bucket containing every child commit
                         that lists it — descendants traversal in O(1) per
                         hop (replaces legacy ``GitCommit.children``).
    * ``by_sha``       — bare-SHA lookup (multi-valued because the same
                         SHA can legitimately exist in two repos when
                         F1's repo-scoped Commit.id allows multi-repo
                         graphs). Mirrors :class:`GitHubCommitRegistry.by_sha`
                         so cross-source joins (``GitHubCommit.sha`` →
                         git :class:`Commit`) work without knowing the
                         owning repo at the join site.
    """

    indexes = [
        IndexSpec(name="by_author", key_fn=lambda c: c.author_ref, multi=True),
        IndexSpec(name="by_committer", key_fn=lambda c: c.committer_ref, multi=True),
        IndexSpec(name="by_project", key_fn=lambda c: c.project_ref, multi=True),
        IndexSpec(name="by_parent", key_fn=lambda c: c.parent_refs, multi=True),
        IndexSpec(name="by_sha", key_fn=lambda c: c.sha, multi=True),
    ]

    def get_id(self, entity: Commit) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


class FileRegistry(Registry[File, str]):
    """Every :class:`File` known to the graph.

    Index choices (plan §4.1):

    * ``by_project``   — one bucket per owning :class:`GitProject` ref.
    * ``by_extension`` — quick "all .py files" lookup; ``None`` keys
                         (extensionless files) are auto-skipped per kernel
                         ``_normalize_keys`` semantics.
    """

    indexes = [
        IndexSpec(name="by_project", key_fn=lambda f: f.project_ref, multi=True),
        IndexSpec(name="by_extension", key_fn=lambda f: f.extension, multi=True),
    ]

    def get_id(self, entity: File) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------


class ChangeRegistry(Registry[Change, str]):
    """Every :class:`Change` seen across the graph.

    Index choices (plan §4.1):

    * ``by_commit``      — fast "all changes in commit X".
    * ``by_file``        — fast "all changes that touched file F".
    * ``by_change_type`` — quick stats / role classifier ("show me every
                           delete").
    """

    indexes = [
        IndexSpec(name="by_commit", key_fn=lambda c: c.commit_ref, multi=True),
        IndexSpec(name="by_file", key_fn=lambda c: c.file_ref, multi=True),
        IndexSpec(
            name="by_change_type", key_fn=lambda c: c.change_type, multi=True
        ),
    ]

    def get_id(self, entity: Change) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Hunks
# ---------------------------------------------------------------------------


class HunkRegistry(Registry[Hunk, str]):
    """Every :class:`Hunk` seen across the graph.

    Hunk is an Entity (plan §1.1 lists ``EntityKind.HUNK``). The only index
    we ship today is ``by_change`` — every other Hunk query (by file, by
    author, by commit) goes via the :class:`Change` ref and its own indexes.
    """

    indexes = [
        IndexSpec(name="by_change", key_fn=lambda h: h.change_ref, multi=True),
    ]

    def get_id(self, entity: Hunk) -> str:
        return entity.id


__all__ = [
    "GitProjectRegistry",
    "GitAccountRegistry",
    "CommitRegistry",
    "FileRegistry",
    "ChangeRegistry",
    "HunkRegistry",
]
