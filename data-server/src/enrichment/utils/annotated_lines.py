"""Per-line attribution replay over the v2 typed git graph.

Faithful port of legacy ``main``'s annotated-lines reconstruction:

* ``Change._apply_line_changes`` (``main:common/git_models.py``) — copy the
  parent change's per-line array, ``pop(line-1)`` for deletes (descending),
  ``insert(line-1, commit)`` for adds.
* ``CommitTransformer._compute_commit_growth`` /
  ``_get_parent_commit_size`` (``main:inspector_git/linker/transformers.py``)
  — ``repo_size = parent[0].repo_size + Σ(added − deleted)`` over the changes
  attributable to the first-parent line.
* ``MergeChangesTransformer._fix_annotated_lines_commits`` — per-line
  reconciliation across the multiple parents of a merge commit (the hard
  part; replicated verbatim, NOT simplified).

The legacy code computed all of this at *graph-construction* time, driving
the per-line array off live Python back-references (``Change.parent_change``,
``Commit.parents``) while it walked the git log in chronological order. v2
dropped the back-references and the build-time hook (see
``common/domains/git/models.py:314-317``); every cross-entity link is now an
:class:`EntityRef` resolved through the typed registries on :class:`Graph`.

This module rebuilds the same result from the hunk stream on demand. It
reuses :mod:`src.enrichment.utils.file_trait_utils`
(``_changes_for_file`` / ``_hunks_for_change`` / ``_resolve_commit``) for the
index walks, and reproduces legacy's chronological commit processing order so
each change reads the *already-replayed* state of its parent change.

Public entry point: :func:`compute_annotated_lines`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from src.enrichment.recent_window import ensure_aware
from src.enrichment.utils.file_trait_utils import (
    _changes_for_file,
    _hunks_for_change,
    _resolve_commit,
)

if TYPE_CHECKING:
    from src.common.kernel import Graph
    from src.common.domains.git.models import Change, Commit, LineChange


# ----------------------------------------------------------------------
# Commit ordering (legacy chronological walk)
# ----------------------------------------------------------------------
def _commit_order(graph: "Graph") -> List["Commit"]:
    """Commits in legacy git-log processing order.

    Legacy (``GitProjectTransformer.transform``) walked the DTO commit list
    such that every commit was created *after* its parents — i.e. a
    parent-before-child topological order, chronological within that
    constraint. We reconstruct it: Kahn topological sort over the
    ``parent_refs`` edges, breaking ties by resolved ``author_date`` (the
    plan's primary key) and then commit id for determinism.

    Doing a real topo sort (rather than a plain date sort) matters because
    a child must never be processed before a parent — author dates can run
    backwards across a merge (rebased / cherry-picked history), and the
    per-line replay reads the parent change's already-computed array.
    """
    commits_reg = getattr(graph, "commits", None)
    if commits_reg is None:
        return []
    commits = list(commits_reg)
    by_id: Dict[str, "Commit"] = {c.id: c for c in commits}

    # parents that actually resolve in this graph (a shallow/partial clone
    # can reference parents we never ingested — treat those as roots).
    parents_of: Dict[str, List[str]] = {}
    children_of: Dict[str, List[str]] = {c.id: [] for c in commits}
    indegree: Dict[str, int] = {c.id: 0 for c in commits}
    for c in commits:
        present = [p.id for p in getattr(c, "parent_refs", []) if p.id in by_id]
        parents_of[c.id] = present
        indegree[c.id] = len(present)
        for pid in present:
            children_of[pid].append(c.id)

    def _sort_key(cid: str):
        c = by_id[cid]
        d = ensure_aware(getattr(c, "author_date", None))
        # ``None`` dates sort first (legacy never produced them, defensive).
        return (d is not None, d, cid)

    # Kahn with a date-ordered frontier.
    ready = [cid for cid in indegree if indegree[cid] == 0]
    ready.sort(key=_sort_key)
    order: List["Commit"] = []
    while ready:
        cid = ready.pop(0)
        order.append(by_id[cid])
        for child in children_of[cid]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
        ready.sort(key=_sort_key)

    if len(order) != len(commits):
        # Cycle (should never happen in a real DAG) — fall back to a stable
        # date sort so we still emit something rather than dropping commits.
        return sorted(commits, key=lambda c: _sort_key(c.id))
    return order


# ----------------------------------------------------------------------
# Hunk → line-change helpers
# ----------------------------------------------------------------------
def _change_line_changes(
    graph: "Graph", change: "Change"
) -> Tuple[List["LineChange"], List["LineChange"]]:
    """Return ``(deleted_lines, added_lines)`` across all hunks of ``change``.

    Hunks are walked in ``ordinal`` order so insert offsets line up with the
    legacy ``line_changes`` flattening (``Change.added_lines`` /
    ``deleted_lines`` iterated hunks in list order).
    """
    deleted: List["LineChange"] = []
    added: List["LineChange"] = []
    hunks = sorted(
        _hunks_for_change(graph, change.ref()),
        key=lambda h: getattr(h, "ordinal", 0),
    )
    for hunk in hunks:
        deleted.extend(getattr(hunk, "deleted_lines", []) or [])
        added.extend(getattr(hunk, "added_lines", []) or [])
    return deleted, added


def _apply_line_changes(
    parent_array: Optional[List[str]],
    deleted: List["LineChange"],
    added: List["LineChange"],
    commit_id: str,
) -> Optional[List[str]]:
    """Replay one change's hunks onto the parent's per-line array.

    Faithful port of ``Change._apply_line_changes``:
      * start from a copy of the parent change's array (empty for ADDs),
      * ``pop(line-1)`` deletes in **descending** line order,
      * ``insert(line-1, commit_id)`` adds in their natural order.

    Stores the *commit id* per line (legacy stored the ``GitCommit`` object;
    the id is the v2-stable surrogate). Returns ``None`` on an ``IndexError``
    — legacy flips ``file.is_binary = True`` and stops attributing; we signal
    the same "give up on this file" outcome to the caller, which cannot
    mutate the frozen ``File`` entity.
    """
    array: List[str] = list(parent_array) if parent_array else []
    try:
        for d in sorted(deleted, key=lambda lc: lc.line_number, reverse=True):
            array.pop(d.line_number - 1)
        for a in added:
            array.insert(a.line_number - 1, commit_id)
    except IndexError:
        return None
    return array


# ----------------------------------------------------------------------
# Merge reconciliation (legacy MergeChangesTransformer._fix_annotated_lines_commits)
# ----------------------------------------------------------------------
def _fix_annotated_lines_commits(
    arrays: List[List[str]],
    missing_array: Optional[List[str]],
    commit_id: str,
) -> List[str]:
    """Reconcile a merge commit's per-parent per-line arrays into one.

    Faithful port of
    ``MergeChangesTransformer._fix_annotated_lines_commits``:

    * ``changes`` here are the merge commit's changes for ONE file, one per
      parent the file diverged on (legacy grouped merge changes by file name
      before calling this). ``arrays[i]`` is the replayed annotated array of
      ``changes[i]``.
    * ``missing_array`` is the file's annotated array taken from a "clean"
      parent — a parent that produced *no* change for this file (so the merge
      took that parent's version verbatim). When present it seeds
      ``arrays[0]`` (legacy: ``changes[0].annotated_lines =
      list(missing_change.annotated_lines)``).
    * Per line index ``i``: if the first parent's line was attributed to the
      merge commit itself, look through the other parents for a line that is
      NOT the merge commit and adopt it (the merge commit didn't really
      author that line — another parent did). This undoes the spurious
      self-attribution a merge's combined diff produces.
    * Finally every change of the merge is given the reconciled array.

    Returns the reconciled array (the value every change of the file shares).
    """
    if not arrays:
        return list(missing_array) if missing_array else []

    if missing_array is not None:
        arrays[0] = list(missing_array)

    base = arrays[0]
    if not base:
        return base

    for i in range(len(base)):
        current = [arr[i] for arr in arrays if i < len(arr)]
        if not current:
            continue
        first_line = current[0]
        rest = current[1:]
        if first_line == commit_id:
            replacement = next((line for line in rest if line != commit_id), None)
            if replacement is not None:
                base[i] = replacement

    return base


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------
def compute_annotated_lines(
    graph: "Graph",
) -> Tuple[Dict[str, List[str]], Dict[str, int]]:
    """Reconstruct per-line commit attribution + per-commit repo_size.

    Walks every commit in legacy chronological (parent-before-child) order,
    replaying each change's hunks onto the parent change's per-line array.
    For merge commits it reconciles the per-parent arrays per file
    (:func:`_fix_annotated_lines_commits`).

    Returns:
        ``(attribution, repo_sizes)`` where

        * ``attribution`` maps ``file.id -> list[str]`` — the surviving
          lines of that file, each carrying the *commit id* that introduced
          it (the file's final ``annotated_lines``; ``len`` is its LOC).
          Binary files and files whose replay overflowed are omitted.
        * ``repo_sizes`` maps ``commit.id -> int`` — the repository line
          count after that commit = first-parent size +
          ``Σ(added − deleted)`` over the first-parent-attributable changes.

    Pure: never mutates the graph.
    """
    # change.id -> its replayed annotated array (the per-change state legacy
    # held on ``Change.annotated_lines``). ``None`` marks a change whose
    # replay overflowed (legacy: file became binary).
    change_arrays: Dict[str, Optional[List[str]]] = {}
    # file.id -> the most recent change's array (final state == file LOC).
    file_final: Dict[str, Optional[List[str]]] = {}
    # file.id -> set True once a file's replay has failed (binary-ised);
    # legacy stops attributing such files for the rest of history.
    failed_files: set[str] = set()
    repo_sizes: Dict[str, int] = {}

    for commit in _commit_order(graph):
        commit_id = commit.id
        parent_refs = list(getattr(commit, "parent_refs", []))
        # first-parent id (legacy ``commit.parents[0]``), if it resolved.
        first_parent_id: Optional[str] = None
        for p in parent_refs:
            if _resolve_commit(graph, p) is not None:
                first_parent_id = p.id
                break

        # ── repo_size: parent[0].repo_size + Σ(added−deleted) over the
        #    first-parent-attributable changes (legacy _compute_commit_growth
        #    + _get_parent_commit_size). ────────────────────────────────────
        base_size = repo_sizes.get(first_parent_id, 0) if first_parent_id else 0
        growth = 0

        is_merge = len(parent_refs) > 1

        # Gather this commit's changes grouped by file so merge reconciliation
        # can act per file (legacy grouped merge changes by file name).
        changes = list(_changes_for_change_commit(graph, commit))
        by_file: Dict[str, List["Change"]] = {}
        for ch in changes:
            by_file.setdefault(ch.file_ref.id, []).append(ch)

        # Legacy ``_compute_commit_growth``: sum over changes where there are
        # no parents OR ``ch.parent_commit == commit.parents[0]``. Binary
        # changes carry no hunks, so they contribute 0 and need no special
        # skip (kept faithful to legacy, which did not skip them either).
        for ch in changes:
            if not parent_refs or (
                ch.parent_commit_ref is not None
                and ch.parent_commit_ref.id == first_parent_id
            ):
                deleted, added = _change_line_changes(graph, ch)
                growth += len(added) - len(deleted)

        repo_sizes[commit_id] = base_size + growth

        # ── per-line replay ───────────────────────────────────────────────
        for file_id, file_changes in by_file.items():
            file_ = file_changes[0].file_ref.resolve(graph)
            if file_ is not None and getattr(file_, "is_binary", False):
                continue
            if file_id in failed_files:
                continue

            # Replay each change of this file at this commit.
            replayed: List[List[str]] = []
            overflow = False
            for ch in file_changes:
                parent_array = _parent_array_for(graph, ch, change_arrays)
                deleted, added = _change_line_changes(graph, ch)
                arr = _apply_line_changes(parent_array, deleted, added, commit_id)
                if arr is None:
                    overflow = True
                    break
                change_arrays[ch.id] = arr
                replayed.append(arr)

            if overflow:
                # Legacy flips file.is_binary; we can't mutate the frozen
                # entity, so we drop the file from attribution and stop
                # replaying it (mirrors "stop attributing this file").
                failed_files.add(file_id)
                file_final[file_id] = None
                for ch in file_changes:
                    change_arrays[ch.id] = None
                continue

            if is_merge:
                # Faithful port of MergeChangesTransformer._fix_changes: for
                # every merge-commit file group, optionally seed from a clean
                # parent (``_missing_parent_array``, fires when this commit has
                # fewer changes for the file than it has parents and they're
                # not all deletes), then reconcile the per-parent arrays
                # per line. Runs even with a single change — main's
                # ``_fix_annotated_lines_commits`` does too, so a 1-change
                # merge that inherited a file verbatim adopts the clean
                # parent's array rather than its own (possibly empty) replay.
                missing_array = _missing_parent_array(
                    graph, commit, file_changes, change_arrays
                )
                reconciled = _fix_annotated_lines_commits(
                    replayed, missing_array, commit_id
                )
                for ch in file_changes:
                    change_arrays[ch.id] = reconciled
                file_final[file_id] = reconciled
            else:
                file_final[file_id] = replayed[-1]

    # Build the final per-file attribution (skip failed / binary files).
    attribution: Dict[str, List[str]] = {}
    for file_id, arr in file_final.items():
        if arr is None or file_id in failed_files:
            continue
        attribution[file_id] = arr
    return attribution, repo_sizes


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------
def _changes_for_change_commit(graph: "Graph", commit: "Commit"):
    """Every change in ``commit`` (via the ChangeRegistry.by_commit index)."""
    changes = getattr(graph, "changes", None)
    if changes is None:
        return ()
    by_commit = getattr(changes, "by_commit", None)
    if by_commit is not None:
        return tuple(by_commit[commit.ref()])
    return tuple(
        ch for ch in changes if getattr(ch, "commit_ref", None) == commit.ref()
    )


def _parent_array_for(
    graph: "Graph",
    change: "Change",
    change_arrays: Dict[str, Optional[List[str]]],
) -> Optional[List[str]]:
    """Resolve the per-line array of a change's parent change.

    Legacy read ``change.parent_change.annotated_lines`` directly. Two paths:

    1. ``parent_change_ref`` is set (synthetic / future graphs that carry it):
       look the replayed array up directly. This already points at the change
       for the *old* path, so renames are followed transparently.
    2. ``parent_change_ref`` is ``None`` (the current v2 bridge never sets it
       — see Chunk 1 handoff): reconstruct legacy
       ``ChangeTransformer.get_last_change`` by walking up from
       ``parent_commit_ref`` for the newest change whose ``new_path`` matches
       this change's ``old_path`` (the file's pre-change name). That follows
       renames too, since ``old_path`` is the name the parent knew the file by.

    ADD changes have no parent → empty array (``None`` here).
    """
    pcr = getattr(change, "parent_change_ref", None)
    if pcr is not None:
        return change_arrays.get(pcr.id)

    pcommit = getattr(change, "parent_commit_ref", None)
    if pcommit is None:
        return None
    parent_change = _last_change_from(graph, pcommit.id, change.old_path)
    if parent_change is None:
        return None
    return change_arrays.get(parent_change.id)


def _missing_parent_array(
    graph: "Graph",
    commit: "Commit",
    file_changes: List["Change"],
    change_arrays: Dict[str, Optional[List[str]]],
) -> Optional[List[str]]:
    """Annotated array from a parent the merge produced *no* change for.

    Faithful port of ``MergeChangesTransformer._get_missing_change``: when a
    merge commit has more parents than it has changes for this file (and the
    changes aren't all deletes), one parent contributed its version verbatim
    (a "clean" parent). We find a parent that none of ``file_changes`` lists
    as its ``parent_commit_ref`` and take that parent change's annotated array
    via the file's last change reachable from that parent.
    """
    from src.common.domains.git.models import ChangeType

    parent_refs = list(getattr(commit, "parent_refs", []))
    if len(file_changes) >= len(parent_refs):
        return None
    if all(c.change_type == ChangeType.DELETE for c in file_changes):
        return None

    used_parent_ids = {
        c.parent_commit_ref.id
        for c in file_changes
        if c.parent_commit_ref is not None
    }
    clean_parent_id = next(
        (p.id for p in parent_refs if p.id not in used_parent_ids), None
    )
    if clean_parent_id is None:
        return None

    # Walk back from the clean parent to the file's last change on that line
    # of history, and return its replayed array.
    last_change = _last_change_from(graph, clean_parent_id, file_changes[0].new_path)
    if last_change is None:
        return None
    return change_arrays.get(last_change.id)


def _last_change_from(
    graph: "Graph", start_commit_id: str, file_name: str
) -> Optional["Change"]:
    """Most recent change to ``file_name`` reachable from ``start_commit_id``.

    Faithful port of ``ChangeTransformer.get_last_change``: DFS up the parent
    chain (first parent preferred) for the newest change whose ``new_path``
    matches ``file_name``.
    """
    commits_reg = getattr(graph, "commits", None)
    changes_reg = getattr(graph, "changes", None)
    if commits_reg is None or changes_reg is None:
        return None
    by_commit = getattr(changes_reg, "by_commit", None)

    stack = [start_commit_id]
    seen: set[str] = set()
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        commit = commits_reg.get(cid)
        if commit is None:
            continue
        if by_commit is not None:
            commit_changes = by_commit[commit.ref()]
        else:
            commit_changes = [
                ch for ch in changes_reg if ch.commit_ref == commit.ref()
            ]
        found = next(
            (c for c in commit_changes if c.new_path == file_name), None
        )
        if found is not None:
            return found
        parent_refs = list(getattr(commit, "parent_refs", []))
        # preserve first-parent preference (legacy pushed reversed -> popped
        # first parent first); we push in reverse so pop() takes parent[0].
        for p in reversed(parent_refs):
            stack.append(p.id)
    return None


__all__ = ["compute_annotated_lines"]
