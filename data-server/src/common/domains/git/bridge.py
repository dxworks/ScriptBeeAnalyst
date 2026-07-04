"""Legacy reader → v2 bundle bridge for the Git domain.

The v2 :class:`GitTransformer` (see ``transformer.py``) consumes an
*already-built* entity bundle. This module is the single seam that walks
the legacy :class:`GitLogDTO` produced by
:class:`src.inspector_git.reader.iglog.readers.ig_log_reader.IGLogReader`
and instantiates the v2 Git-domain entities documented in ``models.py``.

The bridge is intentionally read-only on its inputs and side-effect-free:

* No registry mutation — that is the processor's job.
* No file I/O beyond opening the ``.iglog`` path passed in.
* The returned mapping matches exactly the keys
  :class:`GitTransformer` looks up via ``_BUCKET_SPECS``.

Key translation choices (see Chunk 8 brief):

* :class:`GitAccount` ids are the canonical ``"Name <email>"`` composite
  built via :meth:`GitAccount.make_id` and deduplicated across both
  author and committer slots so the same person isn't instantiated
  twice. NOT repo-scoped — the same person contributing to two repos
  is one :class:`GitAccount` (downstream smart-merge collapses cross-source
  identities, including across repos).
* :class:`Commit` ids are repo-scoped via :meth:`Commit.make_id`
  (``{repo_name}:{sha}``); ``Commit.sha`` carries the bare SHA for
  joins from the GitHub side, indexed via :class:`CommitRegistry.by_sha`.
* :class:`File` ids are repo-scoped via :meth:`File.make_id`
  (``{repo_name}::{path}``); ``File.path`` carries the bare path.
  When a change is a RENAME, both ``old_path`` and ``new_path`` are
  registered as separate :class:`File` entities — the rename-chain
  follow-up that collapses them lives in the transformer layer, not here.
* :class:`Change` ids come from :meth:`Change.make_id` so they're
  deterministic across re-runs. (Change ids include the commit's
  repo-scoped id, so they're naturally repo-scoped too.)

  EXCEPTION — merge commits (>1 parent). A merge's combined diff for a
  single file is emitted by inspector-git as one :class:`ChangeDTO` *per
  parent* (each carrying that parent's ``parent_commit_id``). All of those
  share the same ``(commit_id, old_path, new_path)`` triple, so plain
  :meth:`Change.make_id` would give them one id and the last-write-wins
  :class:`ChangeRegistry` would drop all but one — exactly what legacy
  ``main`` avoided by keeping the per-parent changes as distinct in-memory
  objects (``MergeChangesTransformer``). To preserve the same information,
  a merge change's id is suffixed with its ``parent_commit_id``
  (:func:`_change_id`), so the per-parent changes survive as DISTINCT
  entities and the annotated-lines replay can reconcile them. Non-merge
  (single-parent / root) changes keep the bare :meth:`Change.make_id` id
  byte-for-byte — nothing in the rest of the pipeline changes for them.

  ``parent_change_ref`` is also populated (legacy ``Change.parent_change``):
  it points at the newest change to the file's *old* path reachable from the
  change's ``parent_commit`` — a faithful port of
  ``ChangeTransformer.get_last_change``. Renames are followed because the
  lookup keys on ``old_path`` (the name the parent knew the file by).
* :class:`Hunk` ids come from :meth:`Hunk.make_id` with the change-local
  ordinal (0-based position inside the change's hunk list).
* Legacy :class:`ChangeType` / :class:`LineOperation` enums (plain
  :class:`enum.Enum`) are mapped to the v2 :class:`StrEnum` variants by
  name — they have identical members.
* Commit dates are parsed via :func:`parse_commit_date` (legacy format
  ``"%a %b %d %H:%M:%S %Y %z"``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from src.inspector_git.reader.dto.gitlog.commit_dto import CommitDTO
from src.inspector_git.reader.dto.gitlog.git_log_dto import GitLogDTO
from src.inspector_git.reader.enums.chnage_type import ChangeType as LegacyChangeType
from src.inspector_git.reader.enums.line_operation import (
    LineOperation as LegacyLineOperation,
)
from src.inspector_git.reader.iglog.readers.ig_log_reader import IGLogReader
from src.inspector_git.utils.constants import parse_commit_date

from ...people.source import SourceKind
from .models import (
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


# ---------------------------------------------------------------------------
# Enum mapping helpers
# ---------------------------------------------------------------------------


_CHANGE_TYPE_MAP: Dict[LegacyChangeType, ChangeType] = {
    LegacyChangeType.ADD: ChangeType.ADD,
    LegacyChangeType.DELETE: ChangeType.DELETE,
    LegacyChangeType.RENAME: ChangeType.RENAME,
    LegacyChangeType.MODIFY: ChangeType.MODIFY,
}

_LINE_OP_MAP: Dict[LegacyLineOperation, LineOperation] = {
    LegacyLineOperation.ADD: LineOperation.ADD,
    LegacyLineOperation.DELETE: LineOperation.DELETE,
}


def _map_change_type(value: Any) -> ChangeType:
    """Coerce a legacy :class:`LegacyChangeType` (or its string) to v2."""
    if isinstance(value, LegacyChangeType):
        return _CHANGE_TYPE_MAP[value]
    # Tolerate plain strings — the legacy enum's ``.value`` is identical
    # to the v2 :class:`StrEnum`'s value.
    return ChangeType(value)


def _map_line_operation(value: Any) -> LineOperation:
    if isinstance(value, LegacyLineOperation):
        return _LINE_OP_MAP[value]
    return LineOperation(value)


# ---------------------------------------------------------------------------
# Public bridge entry point
# ---------------------------------------------------------------------------


def build_git_bundle(
    file_path: Path,
    repo_name: str,
    project_name: str = "Project",
) -> Mapping[str, Any]:
    """Parse a legacy ``.iglog`` file and return a v2 entity bundle.

    Parameters
    ----------
    file_path:
        Filesystem path to the ``.iglog`` produced by inspector-git.
    repo_name:
        Stable identifier for the Git repository (used both as the
        :class:`GitProject` ``id`` and as its ``name``). The bridge does
        not invent a uuid — callers pick this so it stays stable across
        re-runs.
    project_name:
        Display ``name`` on the :class:`GitProject`. Defaults to
        ``"Project"`` if the caller has no better label.

    Returns
    -------
    Mapping with the keys :class:`GitTransformer` expects:
    ``{"project", "accounts", "commits", "files", "changes", "hunks"}``.
    """
    log_dto: GitLogDTO = _read_iglog(file_path)

    project = GitProject(
        id=repo_name,
        name=project_name or repo_name,
        source=SourceKind.GIT,
    )
    project_ref = project.ref()

    accounts_by_id: Dict[str, GitAccount] = {}
    files_by_path: Dict[str, File] = {}
    commits: List[Commit] = []
    changes: List[Change] = []
    hunks: List[Hunk] = []

    # Indexes for the legacy ``get_last_change`` parent-change resolution.
    # ``commits_by_id`` lets us walk the parent chain; ``changes_by_commit``
    # holds every change keyed by its commit's repo-scoped id. Both are built
    # incrementally — inspector-git emits commits parent-before-child, so a
    # change's parents are always already indexed when we resolve it.
    commits_by_id: Dict[str, Commit] = {}
    changes_by_commit: Dict[str, List[Change]] = {}

    for commit_dto in log_dto.commits:
        author = _intern_account(
            accounts_by_id,
            commit_dto.author_name,
            commit_dto.author_email,
            project_ref,
        )
        # Some legacy iglog rows reuse the author signature for committer
        # (the reader writes empty strings when the optional committer
        # block is absent). Fall back to the author in that case so the
        # ``committer_ref`` field — required on :class:`Commit` — never
        # points at a phantom ``" <>"`` account.
        if commit_dto.committer_name or commit_dto.committer_email:
            committer = _intern_account(
                accounts_by_id,
                commit_dto.committer_name,
                commit_dto.committer_email,
                project_ref,
            )
        else:
            committer = author

        author_date = parse_commit_date(commit_dto.author_date)
        committer_date = (
            parse_commit_date(commit_dto.committer_date)
            if commit_dto.committer_date
            else author_date
        )

        # ``parent_ids`` from the reader is a ``split(" ")`` result — for
        # root commits that's a single empty string, which we drop.
        # Parents live in the same repo, so they use the same repo_name
        # prefix for their composite id.
        parent_refs = [
            _commit_ref_for(repo_name, parent_sha)
            for parent_sha in commit_dto.parent_ids
            if parent_sha
        ]

        commit_sha = commit_dto.id
        commit = Commit(
            id=Commit.make_id(repo_name, commit_sha),
            sha=commit_sha,
            project_ref=project_ref,
            message=commit_dto.message,
            author_date=author_date,
            committer_date=committer_date,
            author_ref=author.ref(),
            committer_ref=committer.ref(),
            parent_refs=parent_refs,
        )
        commits.append(commit)
        commits_by_id[commit.id] = commit
        commit_ref = commit.ref()

        # A merge commit (>1 parent) emits one change per parent for the same
        # file; we must keep those distinct (see module docstring).
        is_merge = len(parent_refs) > 1
        commit_changes: List[Change] = []

        for ordinal_in_commit, change_dto in enumerate(commit_dto.changes):
            change_type = _map_change_type(change_dto.type)
            old_path = change_dto.old_file_name
            new_path = change_dto.new_file_name

            # Use the *current* (post-change) path as the File id, with
            # the legacy ``dev/null`` sentinel falling back to the other
            # side so a deletion still anchors to a real file.
            file_path_for_id = (
                new_path
                if new_path and new_path != "dev/null"
                else old_path
            )
            file_entity = _intern_file(
                files_by_path, repo_name, file_path_for_id,
                project_ref, change_dto.is_binary,
            )

            parent_commit_sha = change_dto.parent_commit_id or None
            parent_commit_ref = (
                _commit_ref_for(repo_name, parent_commit_sha)
                if parent_commit_sha
                else None
            )

            # Resolve the legacy ``parent_change`` (``get_last_change``): the
            # newest change to ``old_path`` reachable from this change's
            # parent commit. ``None`` for ADDs / when no prior change exists.
            parent_change = (
                _get_last_change(
                    commits_by_id,
                    changes_by_commit,
                    Commit.make_id(repo_name, parent_commit_sha),
                    old_path,
                )
                if parent_commit_sha
                else None
            )

            change = Change(
                id=_change_id(
                    commit.id, old_path, new_path, is_merge, parent_commit_sha
                ),
                commit_ref=commit_ref,
                file_ref=file_entity.ref(),
                change_type=change_type,
                old_path=old_path,
                new_path=new_path,
                parent_commit_ref=parent_commit_ref,
                parent_change_ref=(
                    parent_change.ref() if parent_change is not None else None
                ),
            )

            change_hunks: List[Hunk] = []
            for hunk_ordinal, hunk_dto in enumerate(change_dto.hunks):
                line_changes_v2: List[LineChange] = [
                    LineChange(
                        operation=_map_line_operation(lc.operation),
                        line_number=lc.number,
                        commit_ref=commit_ref,
                    )
                    for lc in hunk_dto.line_changes
                ]
                hunk = Hunk(
                    id=Hunk.make_id(change.id, hunk_ordinal),
                    change_ref=change.ref(),
                    ordinal=hunk_ordinal,
                    line_changes=line_changes_v2,
                )
                change_hunks.append(hunk)
                hunks.append(hunk)

            change.hunk_refs = [h.ref() for h in change_hunks]
            changes.append(change)
            commit_changes.append(change)

        # Register this commit's changes only after the whole commit is built,
        # so a per-parent merge change never resolves its ``parent_change``
        # against a sibling change of the same merge commit.
        changes_by_commit[commit.id] = commit_changes

    return {
        "project": project,
        "accounts": list(accounts_by_id.values()),
        "commits": commits,
        "files": list(files_by_path.values()),
        "changes": changes,
        "hunks": hunks,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _change_id(
    commit_id: str,
    old_path: str,
    new_path: str,
    is_merge: bool,
    parent_commit_sha: Optional[str],
) -> str:
    """Composite id for a :class:`Change`.

    Non-merge (root / single-parent) commits use the legacy
    :meth:`Change.make_id` triple verbatim — their ids are unchanged from
    before this fix, so nothing downstream of the bridge shifts for them.

    Merge commits (>1 parent) emit one change per parent for the same file,
    all sharing the ``(commit_id, old_path, new_path)`` triple. To keep them
    DISTINCT (legacy held them as separate objects; the v2 registry is
    last-write-wins and would otherwise drop all but one), the merge change's
    id is suffixed with its ``parent_commit_id``. Mirrors how ``main``'s
    ``MergeChangesTransformer`` preserved per-parent changes. The ``^``
    separator can't appear in the base id (which uses ``-`` and ``->``).
    """
    base = Change.make_id(commit_id, old_path, new_path)
    if is_merge and parent_commit_sha:
        return f"{base}^{parent_commit_sha}"
    return base


def _get_last_change(
    commits_by_id: Dict[str, Commit],
    changes_by_commit: Dict[str, List[Change]],
    start_commit_id: str,
    file_name: str,
) -> Optional[Change]:
    """Newest change whose ``new_path`` == ``file_name``, reachable upward.

    Faithful port of legacy ``ChangeTransformer.get_last_change``: DFS up the
    parent chain from ``start_commit_id`` (first parent preferred), returning
    the first change that produced ``file_name``. Renames are followed because
    the caller keys on the change's ``old_path``. Returns ``None`` if no such
    change exists in the ingested history (legacy raised ``NoChangeException``;
    here a missing parent change simply leaves ``parent_change_ref`` unset and
    the replay falls back to its ``parent_commit_ref`` walk).
    """
    stack = [start_commit_id]
    seen: set[str] = set()
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        for ch in changes_by_commit.get(cid, ()):
            if ch.new_path == file_name:
                return ch
        commit = commits_by_id.get(cid)
        if commit is None:
            continue
        # Push parents in reverse so pop() visits the first parent first.
        for p in reversed(list(commit.parent_refs)):
            stack.append(p.id)
    return None


def _read_iglog(file_path: Path) -> GitLogDTO:
    """Open the iglog file and run the legacy reader against it."""
    with open(file_path, "r", encoding="utf-8") as stream:
        return IGLogReader().read(stream)


def _intern_account(
    cache: Dict[str, GitAccount],
    name: str,
    email: str,
    project_ref: Any,
) -> GitAccount:
    """Return a cached :class:`GitAccount` for ``(name, email)``."""
    account_id = GitAccount.make_id(name, email)
    account = cache.get(account_id)
    if account is None:
        account = GitAccount(
            id=account_id,
            name=name,
            email=email,
            project_ref=project_ref,
        )
        cache[account_id] = account
    return account


def _intern_file(
    cache: Dict[str, File],
    repo_name: str,
    path: str,
    project_ref: Any,
    is_binary: bool,
) -> File:
    """Return a cached :class:`File` for ``(repo_name, path)``.

    The cache key is the bare path (cheap and matches the legacy semantics
    of "same path across commits collapses to one File"). The constructed
    :class:`File`'s id is repo-scoped (:meth:`File.make_id`).

    If the same path appears twice across commits (typical) we keep the
    *first* :class:`File` we built. ``is_binary`` is OR-ed so a path that
    was ever binary in the history stays marked binary.
    """
    existing = cache.get(path)
    if existing is None:
        existing = File(
            id=File.make_id(repo_name, path),
            project_ref=project_ref,
            path=path,
            is_binary=is_binary,
            extension=File.derive_extension(path),
        )
        cache[path] = existing
    elif is_binary and not existing.is_binary:
        # ``File`` is a regular Pydantic model (not frozen), so attribute
        # assignment is allowed and updates the cached instance.
        existing.is_binary = True
    return existing


def _commit_ref_for(repo_name: str, sha: str):
    """Build an :class:`EntityRef` to a :class:`Commit` by its repo-scoped id.

    We don't keep a dict of commits here — refs are pure value objects
    keyed by ``(kind, id)``, so this avoids forcing parent commits to be
    parsed before their children.
    """
    # ``Commit.ref()`` would need an instance; mirror its construction.
    from ...kernel import EntityRef

    return EntityRef(kind=Commit.kind, id=Commit.make_id(repo_name, sha))


__all__ = ["build_git_bundle"]
