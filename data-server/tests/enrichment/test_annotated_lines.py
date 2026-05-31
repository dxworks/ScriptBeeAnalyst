"""Unit tests for the per-line attribution replay utility
(:func:`src.enrichment.utils.annotated_lines.compute_annotated_lines`).

These build small synthetic v2 graphs by hand (the conftest ``make_change``
helper doesn't wire ``parent_change_ref`` / ``parent_commit_ref``, which the
replay needs), exercising each branch of the faithful port of legacy
``main``'s annotated-lines reconstruction:

* pure adds (ADD change from empty),
* deletes (descending-line pop),
* mixed add+delete ordering within one change,
* rename (parent state followed across the old path),
* binary skip,
* a merge commit (per-line reconciliation across two parents).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
from src.common.kernel import Graph
from src.common.people import SourceKind
from src.enrichment.utils.annotated_lines import compute_annotated_lines


UTC = timezone.utc
T0 = datetime(2021, 1, 1, tzinfo=UTC)


# ----------------------------------------------------------------------
# Builders (explicit parent wiring)
# ----------------------------------------------------------------------
def _graph() -> tuple[Graph, GitProject, GitAccount]:
    graph = Graph(project_id="al")
    project = GitProject(id="gp:al", name="al", source=SourceKind.GIT)
    graph.add_project(project)
    acct = GitAccount(
        id=GitAccount.make_id("Alice", "alice@example.com"),
        name="Alice",
        email="alice@example.com",
        project_ref=project.ref(),
    )
    graph.git_accounts.add(acct)
    return graph, project, acct


def _commit(
    graph: Graph,
    project: GitProject,
    acct: GitAccount,
    sha: str,
    when: datetime,
    parents: list[str] | None = None,
) -> Commit:
    c = Commit(
        id=sha,
        sha=sha,
        project_ref=project.ref(),
        message=sha,
        author_date=when,
        committer_date=when,
        author_ref=acct.ref(),
        committer_ref=acct.ref(),
        parent_refs=[
            graph.commits.get(p).ref() for p in (parents or [])
        ],
    )
    graph.commits.add(c)
    return c


def _file(graph: Graph, project: GitProject, path: str, is_binary: bool = False) -> File:
    f = File(
        id=path,
        path=path,
        project_ref=project.ref(),
        is_binary=is_binary,
        extension=File.derive_extension(path),
    )
    graph.files.add(f)
    return f


def _change(
    graph: Graph,
    commit: Commit,
    file_: File,
    *,
    change_type: ChangeType,
    adds: list[int] | None = None,
    deletes: list[int] | None = None,
    old_path: str | None = None,
    new_path: str | None = None,
    parent_commit: Commit | None = None,
    parent_change: Change | None = None,
    id_suffix: str = "",
) -> Change:
    op = old_path or file_.path
    np = new_path or file_.path
    ch = Change(
        id=Change.make_id(commit.id, op, np) + id_suffix,
        commit_ref=commit.ref(),
        file_ref=file_.ref(),
        change_type=change_type,
        old_path=op,
        new_path=np,
        parent_commit_ref=parent_commit.ref() if parent_commit else None,
        parent_change_ref=parent_change.ref() if parent_change else None,
    )
    line_changes = [
        LineChange(operation=LineOperation.ADD, line_number=n, commit_ref=commit.ref())
        for n in (adds or [])
    ] + [
        LineChange(operation=LineOperation.DELETE, line_number=n, commit_ref=commit.ref())
        for n in (deletes or [])
    ]
    hunk = Hunk(
        id=Hunk.make_id(ch.id, 0),
        change_ref=ch.ref(),
        ordinal=0,
        line_changes=line_changes,
    )
    ch.hunk_refs = [hunk.ref()]
    graph.changes.add(ch)
    graph.hunks.add(hunk)
    return ch


# ----------------------------------------------------------------------
# Pure adds
# ----------------------------------------------------------------------
def test_pure_add_from_empty():
    graph, project, acct = _graph()
    c1 = _commit(graph, project, acct, "c1", T0)
    f = _file(graph, project, "a.py")
    _change(
        graph, c1, f,
        change_type=ChangeType.ADD,
        adds=[1, 2, 3],
        parent_commit=None,
    )

    attribution, repo_sizes = compute_annotated_lines(graph)

    assert attribution[f.id] == ["c1", "c1", "c1"]
    assert repo_sizes["c1"] == 3


def test_adds_accumulate_across_commits():
    graph, project, acct = _graph()
    c1 = _commit(graph, project, acct, "c1", T0)
    c2 = _commit(graph, project, acct, "c2", T0 + timedelta(days=1), parents=["c1"])
    f = _file(graph, project, "a.py")
    ch1 = _change(graph, c1, f, change_type=ChangeType.ADD, adds=[1, 2])
    # c2 appends a third line at the end.
    _change(
        graph, c2, f,
        change_type=ChangeType.MODIFY,
        adds=[3],
        parent_commit=c1,
        parent_change=ch1,
    )

    attribution, repo_sizes = compute_annotated_lines(graph)

    assert attribution[f.id] == ["c1", "c1", "c2"]
    assert repo_sizes["c1"] == 2
    assert repo_sizes["c2"] == 3


# ----------------------------------------------------------------------
# Deletes
# ----------------------------------------------------------------------
def test_delete_pops_descending():
    graph, project, acct = _graph()
    c1 = _commit(graph, project, acct, "c1", T0)
    c2 = _commit(graph, project, acct, "c2", T0 + timedelta(days=1), parents=["c1"])
    f = _file(graph, project, "a.py")
    ch1 = _change(graph, c1, f, change_type=ChangeType.ADD, adds=[1, 2, 3, 4])
    # delete lines 2 and 4 (descending pop => removes the right ones).
    _change(
        graph, c2, f,
        change_type=ChangeType.MODIFY,
        deletes=[2, 4],
        parent_commit=c1,
        parent_change=ch1,
    )

    attribution, repo_sizes = compute_annotated_lines(graph)

    # original [c1,c1,c1,c1]; remove idx 3 then idx 1 -> [c1, c1] (lines 1,3).
    assert attribution[f.id] == ["c1", "c1"]
    assert repo_sizes["c2"] == 2  # 4 - 2


# ----------------------------------------------------------------------
# Mixed add + delete ordering within one change
# ----------------------------------------------------------------------
def test_mixed_add_delete_in_one_change():
    graph, project, acct = _graph()
    c1 = _commit(graph, project, acct, "c1", T0)
    c2 = _commit(graph, project, acct, "c2", T0 + timedelta(days=1), parents=["c1"])
    f = _file(graph, project, "a.py")
    ch1 = _change(graph, c1, f, change_type=ChangeType.ADD, adds=[1, 2, 3])
    # delete line 2, then insert two new lines at positions 2 and 3.
    _change(
        graph, c2, f,
        change_type=ChangeType.MODIFY,
        deletes=[2],
        adds=[2, 3],
        parent_commit=c1,
        parent_change=ch1,
    )

    attribution, _ = compute_annotated_lines(graph)

    # start [c1,c1,c1]; delete idx1 -> [c1,c1]; insert@1 c2 -> [c1,c2,c1];
    # insert@2 c2 -> [c1,c2,c2,c1].
    assert attribution[f.id] == ["c1", "c2", "c2", "c1"]


# ----------------------------------------------------------------------
# Rename — parent state followed across the old path
# ----------------------------------------------------------------------
def test_rename_follows_parent_state():
    graph, project, acct = _graph()
    c1 = _commit(graph, project, acct, "c1", T0)
    c2 = _commit(graph, project, acct, "c2", T0 + timedelta(days=1), parents=["c1"])
    f = _file(graph, project, "old.py")
    ch1 = _change(graph, c1, f, change_type=ChangeType.ADD, adds=[1, 2])
    # rename old.py -> new.py, adding one line. Same File entity, new path.
    _change(
        graph, c2, f,
        change_type=ChangeType.RENAME,
        adds=[3],
        old_path="old.py",
        new_path="new.py",
        parent_commit=c1,
        parent_change=ch1,
    )

    attribution, _ = compute_annotated_lines(graph)

    # inherited [c1,c1] then add line 3 -> [c1,c1,c2].
    assert attribution[f.id] == ["c1", "c1", "c2"]


# ----------------------------------------------------------------------
# Binary skip
# ----------------------------------------------------------------------
def test_binary_file_skipped():
    graph, project, acct = _graph()
    c1 = _commit(graph, project, acct, "c1", T0)
    f = _file(graph, project, "logo.png", is_binary=True)
    # Binary changes carry no hunks in practice.
    _change(graph, c1, f, change_type=ChangeType.ADD, parent_commit=None)

    attribution, repo_sizes = compute_annotated_lines(graph)

    assert f.id not in attribution
    assert repo_sizes["c1"] == 0


# ----------------------------------------------------------------------
# Merge commit — per-line reconciliation across two parents
# ----------------------------------------------------------------------
def test_merge_commit_reconciles_self_attribution():
    """A merge commit's combined diff can attribute a line to the merge
    itself; reconciliation must adopt a real parent's attribution instead.

    History:
        c1 (base, adds line1)
        /        \\
      c2(b1)     c3(b2)   each adds one line on top of c1
        \\        /
          m  (merge, two changes for the file: one per parent)
    """
    graph, project, acct = _graph()
    c1 = _commit(graph, project, acct, "c1", T0)
    c2 = _commit(graph, project, acct, "c2", T0 + timedelta(days=1), parents=["c1"])
    c3 = _commit(graph, project, acct, "c3", T0 + timedelta(days=2), parents=["c1"])
    m = _commit(graph, project, acct, "m", T0 + timedelta(days=3), parents=["c2", "c3"])

    f = _file(graph, project, "a.py")
    base = _change(graph, c1, f, change_type=ChangeType.ADD, adds=[1])
    # c2 adds line 2 (c2's content); c3 adds line 2 (c3's content) on its
    # own branch. The merged file ends up [c1, c2-line, c3-line].
    ch2 = _change(
        graph, c2, f, change_type=ChangeType.MODIFY, adds=[2],
        parent_commit=c1, parent_change=base,
    )
    ch3 = _change(
        graph, c3, f, change_type=ChangeType.MODIFY, adds=[2],
        parent_commit=c1, parent_change=base,
    )
    # Merge records one change per parent (the combined diff). In a real v2
    # graph these two would collide on Change.make_id (same commit+path) and
    # the registry would keep only one — see the Chunk 1 handoff "merge
    # collision" note. We give them distinct id suffixes here to exercise the
    # multi-array reconciliation algorithm itself (a faithful port of legacy
    # ``_fix_annotated_lines_commits``), which is what main fed it.
    #   vs c2: c3's line is "new" at line 3 -> [c1, c2, m]
    #   vs c3: c2's line is "new" at line 2 -> [c1, m, c3]
    # Reconciliation must turn the self-attributed 'm' into the real parent.
    _change(
        graph, m, f, change_type=ChangeType.MODIFY, adds=[3],
        parent_commit=c2, parent_change=ch2, id_suffix="@c2",
    )
    _change(
        graph, m, f, change_type=ChangeType.MODIFY, adds=[2],
        parent_commit=c3, parent_change=ch3, id_suffix="@c3",
    )

    attribution, repo_sizes = compute_annotated_lines(graph)

    result = attribution[f.id]
    # The merge self-attribution is reconciled away: every surviving line is
    # owned by a real authoring parent, not the merge commit.
    assert "m" not in result
    assert sorted(result) == ["c1", "c2", "c3"]
    # repo_size grows only along the first parent (c2: size 2), plus the
    # first-parent change growth (+1 add) -> 3.
    assert repo_sizes["c2"] == 2
    assert repo_sizes["m"] == 3


def test_merge_with_clean_parent_seeds_missing_array():
    """Merge with fewer changes than parents: the "missing" parent
    contributed the file verbatim, so its array seeds the result.

    Faithful port of legacy ``_get_missing_change`` +
    ``_fix_annotated_lines_commits`` (the ``missing_change`` branch): when a
    merge has only one change for a file but two parents, the OTHER (clean)
    parent's version wins — legacy overwrites ``changes[0].annotated_lines``
    with the clean parent's array, discarding the lone change's own replay.

    History: c2 branch adds a 3rd line; c3 branch leaves the file at 2 lines.
    The merge records a single change (vs c2). The clean parent is c3, whose
    a.py is the unmodified base ``[c1, c1]`` -> that's what the merge adopts.
    """
    graph, project, acct = _graph()
    c1 = _commit(graph, project, acct, "c1", T0)
    c2 = _commit(graph, project, acct, "c2", T0 + timedelta(days=1), parents=["c1"])
    c3 = _commit(graph, project, acct, "c3", T0 + timedelta(days=2), parents=["c1"])
    m = _commit(graph, project, acct, "m", T0 + timedelta(days=3), parents=["c2", "c3"])

    f = _file(graph, project, "a.py")
    base = _change(graph, c1, f, change_type=ChangeType.ADD, adds=[1, 2])
    # c2 adds a 3rd line; c3 does not touch the file (clean parent).
    ch2 = _change(
        graph, c2, f, change_type=ChangeType.MODIFY, adds=[3],
        parent_commit=c1, parent_change=base,
    )
    # Merge has a single change for the file (vs c2) but two parents
    # -> clean-parent (c3) seeding engages.
    _change(
        graph, m, f, change_type=ChangeType.MODIFY, adds=[],
        parent_commit=c2, parent_change=ch2,
    )

    attribution, _ = compute_annotated_lines(graph)

    # The clean parent c3's verbatim base array wins.
    assert attribution[f.id] == ["c1", "c1"]
