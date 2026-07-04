"""Bridge-level tests for merge-commit change handling + parent linkage.

These exercise the seam that closes the Chunk-1 gap: a merge commit's
per-parent changes for one file must survive the bridge as DISTINCT
:class:`Change` entities (legacy ``MergeChangesTransformer`` kept them as
separate objects; the v2 ``ChangeRegistry`` is last-write-wins and would
otherwise collapse them onto a single ``Change.make_id`` id). We also assert:

* single-parent / root change ids are UNCHANGED (the critical safety
  constraint — anything else ripples through every registry/index),
* ``parent_change_ref`` is populated (legacy ``Change.parent_change``),
* the Chunk-1 annotated-lines replay (:func:`compute_annotated_lines`) now
  fires per-parent reconciliation on a real bridge-built graph and produces
  the same attribution main produced.
"""
from __future__ import annotations

from typing import List
from unittest import mock

from src.common.domains.git import bridge as git_bridge
from src.common.domains.git.bridge import build_git_bundle
from src.common.domains.git.models import Change, Commit
from src.common.kernel import Graph
from src.inspector_git.reader.dto.gitlog.chnage_dto import ChangeDTO
from src.inspector_git.reader.dto.gitlog.commit_dto import CommitDTO
from src.inspector_git.reader.dto.gitlog.git_log_dto import GitLogDTO
from src.inspector_git.reader.dto.gitlog.hunk_dto import HunkDTO
from src.inspector_git.reader.dto.gitlog.line_chnage_dto import LineChangeDTO
from src.inspector_git.reader.enums.chnage_type import ChangeType as DTOChangeType
from src.inspector_git.reader.enums.line_operation import LineOperation as DTOLineOp


REPO = "repo"


def _date(day: int) -> str:
    # Format expected by parse_commit_date: "%a %b %d %H:%M:%S %Y %z".
    return f"Fri Jan {day:02d} 12:00:00 2021 +0000"


def _adds(*line_numbers: int) -> List[HunkDTO]:
    if not line_numbers:
        return []
    return [
        HunkDTO(
            line_changes=[
                LineChangeDTO(operation=DTOLineOp.ADD, number=n) for n in line_numbers
            ]
        )
    ]


def _change(
    *,
    old: str,
    new: str,
    ctype: DTOChangeType,
    parent_commit_id: str,
    hunks: List[HunkDTO],
) -> ChangeDTO:
    return ChangeDTO(
        old_file_name=old,
        new_file_name=new,
        type=ctype,
        parent_commit_id=parent_commit_id,
        is_binary=False,
        hunks=hunks,
    )


def _commit(
    sha: str, parents: List[str], day: int, changes: List[ChangeDTO]
) -> CommitDTO:
    return CommitDTO(
        id=sha,
        parent_ids=parents,
        author_name="Alice",
        author_email="alice@example.com",
        author_date=_date(day),
        committer_name="Alice",
        committer_email="alice@example.com",
        committer_date=_date(day),
        message=sha,
        changes=changes,
    )


def _merge_log() -> GitLogDTO:
    """A diamond: c1 -> {c2, c3} -> m, all touching ``a.py``.

    The merge ``m`` records one change PER PARENT for ``a.py`` (each with a
    distinct ``parent_commit_id``) — exactly the shape inspector-git emits
    and the shape main's ``MergeChangesTransformer`` kept distinct.
    """
    c1 = _commit("c1", [], 1, [_change(
        old="a.py", new="a.py", ctype=DTOChangeType.ADD,
        parent_commit_id="", hunks=_adds(1),
    )])
    c2 = _commit("c2", ["c1"], 2, [_change(
        old="a.py", new="a.py", ctype=DTOChangeType.MODIFY,
        parent_commit_id="c1", hunks=_adds(2),
    )])
    c3 = _commit("c3", ["c1"], 3, [_change(
        old="a.py", new="a.py", ctype=DTOChangeType.MODIFY,
        parent_commit_id="c1", hunks=_adds(2),
    )])
    m = _commit("m", ["c2", "c3"], 4, [
        _change(
            old="a.py", new="a.py", ctype=DTOChangeType.MODIFY,
            parent_commit_id="c2", hunks=_adds(3),
        ),
        _change(
            old="a.py", new="a.py", ctype=DTOChangeType.MODIFY,
            parent_commit_id="c3", hunks=_adds(2),
        ),
    ])
    return GitLogDTO(commits=[c1, c2, c3, m])


def _bundle(log: GitLogDTO):
    with mock.patch.object(git_bridge, "_read_iglog", return_value=log):
        return build_git_bundle(file_path=None, repo_name=REPO, project_name="p")  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Distinct per-parent merge changes
# ----------------------------------------------------------------------
def test_merge_per_parent_changes_are_distinct():
    bundle = _bundle(_merge_log())
    changes: List[Change] = bundle["changes"]

    m_id = Commit.make_id(REPO, "m")
    merge_changes = [c for c in changes if c.commit_ref.id == m_id]
    # Two parents -> two surviving, DISTINCT changes for a.py.
    assert len(merge_changes) == 2
    assert len({c.id for c in merge_changes}) == 2
    # Each merge change is suffixed with its own parent commit's id.
    suffixes = {c.id.rsplit("^", 1)[1] for c in merge_changes}
    assert suffixes == {"c2", "c3"}
    # They point at distinct parent commits.
    assert {c.parent_commit_ref.id for c in merge_changes} == {
        Commit.make_id(REPO, "c2"),
        Commit.make_id(REPO, "c3"),
    }


def test_single_parent_change_ids_unchanged():
    """The safety constraint: non-merge change ids must be byte-for-byte the
    legacy ``Change.make_id`` triple — no parent suffix, no other shift."""
    bundle = _bundle(_merge_log())
    changes: List[Change] = bundle["changes"]
    by_commit = {}
    for c in changes:
        by_commit.setdefault(c.commit_ref.id, []).append(c)

    for sha in ("c1", "c2", "c3"):
        cid = Commit.make_id(REPO, sha)
        non_merge = by_commit[cid]
        assert len(non_merge) == 1
        ch = non_merge[0]
        assert ch.id == Change.make_id(cid, "a.py", "a.py")
        assert "^" not in ch.id


def test_parent_change_ref_populated():
    bundle = _bundle(_merge_log())
    changes: List[Change] = bundle["changes"]
    by_id = {c.id: c for c in changes}

    root = by_id[Change.make_id(Commit.make_id(REPO, "c1"), "a.py", "a.py")]
    # Root ADD has no parent change.
    assert root.parent_change_ref is None

    # c2 / c3 inherit a.py from c1's change.
    c2_change = by_id[Change.make_id(Commit.make_id(REPO, "c2"), "a.py", "a.py")]
    assert c2_change.parent_change_ref is not None
    assert c2_change.parent_change_ref.id == root.id

    # Each merge per-parent change links to its own parent's change.
    m_id = Commit.make_id(REPO, "m")
    merge_changes = {
        c.id.rsplit("^", 1)[1]: c for c in changes if c.commit_ref.id == m_id
    }
    assert merge_changes["c2"].parent_change_ref is not None
    assert (
        merge_changes["c2"].parent_change_ref.id
        == by_id[Change.make_id(Commit.make_id(REPO, "c2"), "a.py", "a.py")].id
    )
    assert (
        merge_changes["c3"].parent_change_ref.id
        == by_id[Change.make_id(Commit.make_id(REPO, "c3"), "a.py", "a.py")].id
    )


# ----------------------------------------------------------------------
# End-to-end: Chunk-1 reconciliation fires on a real bridge graph
# ----------------------------------------------------------------------
def _graph_from_bundle(bundle) -> Graph:
    graph = Graph(project_id=REPO)
    graph.add_project(bundle["project"])
    for a in bundle["accounts"]:
        graph.git_accounts.add(a)
    for c in bundle["commits"]:
        graph.commits.add(c)
    for f in bundle["files"]:
        graph.files.add(f)
    for ch in bundle["changes"]:
        graph.changes.add(ch)
    for h in bundle["hunks"]:
        graph.hunks.add(h)
    return graph


def test_merge_reconciliation_fires_end_to_end():
    """The Chunk-1 per-parent reconciliation now executes on a real merge
    built by the bridge — every surviving line is attributed to a real
    authoring parent, never to the merge commit itself."""
    from src.enrichment.utils.annotated_lines import compute_annotated_lines

    graph = _graph_from_bundle(_bundle(_merge_log()))
    attribution, repo_sizes = compute_annotated_lines(graph)

    file_id = next(iter(attribution))
    result = attribution[file_id]
    m_id = Commit.make_id(REPO, "m")
    # Self-attribution reconciled away; the file is [c1, c2, c3] content.
    assert m_id not in result
    assert sorted(result) == [
        Commit.make_id(REPO, "c1"),
        Commit.make_id(REPO, "c2"),
        Commit.make_id(REPO, "c3"),
    ]
    # repo_size grows along the first parent only.
    assert repo_sizes[Commit.make_id(REPO, "c2")] == 2
    assert repo_sizes[m_id] == 3
