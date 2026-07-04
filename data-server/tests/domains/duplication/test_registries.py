"""Duplication-domain registry tests."""
from __future__ import annotations

from pathlib import Path

from src.common.domains.duplication import (
    DuplicationKind,
    DuplicationPair,
    DuplicationPairRegistry,
    DuplicationProject,
    DuplicationProjectRegistry,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind
from src.common.pickle_store import PickleStore


PROJECT_ID = "dup-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)
FILE_A = EntityRef(kind=EntityKind.FILE, id="src/A.java")
FILE_B = EntityRef(kind=EntityKind.FILE, id="src/B.java")
FILE_C = EntityRef(kind=EntityKind.FILE, id="src/C.java")


def _pair(
    a: EntityRef, b: EntityRef, tokens: int = 50,
    kind: DuplicationKind = DuplicationKind.EXTERNAL,
) -> DuplicationPair:
    return DuplicationPair(
        id=DuplicationPair.make_id(a.id, b.id),
        project_ref=PROJECT_REF,
        file_a_ref=a,
        file_b_ref=b,
        token_count=tokens,
        duplication_kind=kind,
    )


# ---------------------------------------------------------------------------
# DuplicationProjectRegistry
# ---------------------------------------------------------------------------


def test_duplication_project_registry_indexes():
    reg = DuplicationProjectRegistry()
    p1 = DuplicationProject(id="p1", name="X", source=SourceKind.DUPLICATION)
    p2 = DuplicationProject(id="p2", name="Y", source=SourceKind.DUPLICATION)
    reg.add(p1)
    reg.add(p2)
    assert {p.id for p in reg.by_name["X"]} == {"p1"}
    assert {p.id for p in reg.by_name["Y"]} == {"p2"}


# ---------------------------------------------------------------------------
# DuplicationPairRegistry
# ---------------------------------------------------------------------------


def test_duplication_pair_registry_indexes():
    reg = DuplicationPairRegistry()
    pair_ab = _pair(FILE_A, FILE_B)
    pair_ac = _pair(FILE_A, FILE_C)
    pair_bc_sib = _pair(FILE_B, FILE_C, kind=DuplicationKind.SIBLING)
    pair_aa = _pair(FILE_A, FILE_A, kind=DuplicationKind.INTERNAL)
    reg.add(pair_ab)
    reg.add(pair_ac)
    reg.add(pair_bc_sib)
    reg.add(pair_aa)

    # by_file_a / by_file_b respect the canonical ordering
    assert {p.id for p in reg.by_file_a[FILE_A]} == {
        pair_ab.id,
        pair_ac.id,
        pair_aa.id,
    }
    # by_file is the fan-out: every pair touching the file regardless of side
    assert {p.id for p in reg.by_file[FILE_A]} == {
        pair_ab.id,
        pair_ac.id,
        pair_aa.id,
    }
    assert {p.id for p in reg.by_file[FILE_B]} == {
        pair_ab.id,
        pair_bc_sib.id,
    }
    assert {p.id for p in reg.by_file[FILE_C]} == {
        pair_ac.id,
        pair_bc_sib.id,
    }
    assert {p.id for p in reg.by_kind[DuplicationKind.EXTERNAL]} == {
        pair_ab.id,
        pair_ac.id,
    }
    assert {p.id for p in reg.by_kind[DuplicationKind.SIBLING]} == {pair_bc_sib.id}
    assert {p.id for p in reg.by_kind[DuplicationKind.INTERNAL]} == {pair_aa.id}
    assert {p.id for p in reg.by_project[PROJECT_REF]} == {
        pair_ab.id,
        pair_ac.id,
        pair_bc_sib.id,
        pair_aa.id,
    }


def test_duplication_pair_registry_internal_pair_fans_out_once():
    """A self-pair (internal duplication) must not appear twice in
    ``by_file[FILE_X]``."""
    reg = DuplicationPairRegistry()
    pair_aa = _pair(FILE_A, FILE_A, kind=DuplicationKind.INTERNAL)
    reg.add(pair_aa)
    # by_file[FILE_A] returns the row exactly once, not duplicated.
    bucket = reg.by_file[FILE_A]
    assert len(bucket) == 1
    assert bucket[0].id == pair_aa.id


def test_duplication_pair_registry_remove_updates_indexes():
    reg = DuplicationPairRegistry()
    pair_ab = _pair(FILE_A, FILE_B)
    reg.add(pair_ab)
    assert reg.by_file[FILE_A] == (pair_ab,)
    assert reg.by_file[FILE_B] == (pair_ab,)
    reg.remove(pair_ab.id)
    assert reg.by_file[FILE_A] == ()
    assert reg.by_file[FILE_B] == ()
    assert reg.by_file_a[FILE_A] == ()


# ---------------------------------------------------------------------------
# Pickle round-trip
# ---------------------------------------------------------------------------


def test_duplication_pair_registry_pickle_round_trip(tmp_path: Path):
    reg = DuplicationPairRegistry()
    reg.add(_pair(FILE_A, FILE_B))
    reg.add(_pair(FILE_A, FILE_C))
    store = PickleStore(tmp_path)
    store.write_registry(EntityKind.DUPLICATION_PAIR.value, reg)
    restored = store.read_registry(
        EntityKind.DUPLICATION_PAIR.value, DuplicationPairRegistry
    )
    assert len(restored) == 2
    assert {p.id for p in restored.by_file[FILE_A]} == {
        DuplicationPair.make_id(FILE_A.id, FILE_B.id),
        DuplicationPair.make_id(FILE_A.id, FILE_C.id),
    }
