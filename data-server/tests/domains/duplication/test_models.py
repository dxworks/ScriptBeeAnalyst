"""Duplication-domain entity construction tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.common.domains.duplication import (
    DuplicationKind,
    DuplicationPair,
    DuplicationProject,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "dup-proj-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)
FILE_A_REF = EntityRef(kind=EntityKind.FILE, id="src/A.java")
FILE_B_REF = EntityRef(kind=EntityKind.FILE, id="src/B.java")


def test_duplication_project_construct():
    p = DuplicationProject(
        id=PROJECT_ID, name="ZEPPELIN", source=SourceKind.DUPLICATION
    )
    assert p.kind == EntityKind.PROJECT
    assert p.source == SourceKind.DUPLICATION


def test_duplication_project_transformer_class():
    from src.common.domains.duplication.transformer import (
        DuplicationTransformer,
    )

    p = DuplicationProject(
        id=PROJECT_ID, name="z", source=SourceKind.DUPLICATION
    )
    assert p.transformer_class() is DuplicationTransformer


def test_duplication_project_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        DuplicationProject(
            id=PROJECT_ID,
            name="z",
            source=SourceKind.DUPLICATION,
            mystery=1,  # type: ignore[call-arg]
        )


def test_duplication_pair_construct_external():
    pair = DuplicationPair(
        id=DuplicationPair.make_id(FILE_A_REF.id, FILE_B_REF.id),
        project_ref=PROJECT_REF,
        file_a_ref=FILE_A_REF,
        file_b_ref=FILE_B_REF,
        token_count=80,
        block_count=2,
        duplication_kind=DuplicationKind.EXTERNAL,
    )
    assert pair.kind == EntityKind.DUPLICATION_PAIR
    assert pair.token_count == 80
    assert pair.block_count == 2
    assert pair.duplication_kind == DuplicationKind.EXTERNAL
    assert pair.line_range_a is None
    assert pair.similarity_score is None


def test_duplication_pair_make_id_canonicalises_order():
    a = "src/Zeta.java"
    b = "src/Alpha.java"
    assert DuplicationPair.make_id(a, b) == DuplicationPair.make_id(b, a)
    expected = f"{b}::{a}"  # sorted lexically
    assert DuplicationPair.make_id(a, b) == expected


def test_duplication_pair_internal_self_pair():
    pair = DuplicationPair(
        id=DuplicationPair.make_id(FILE_A_REF.id, FILE_A_REF.id),
        project_ref=PROJECT_REF,
        file_a_ref=FILE_A_REF,
        file_b_ref=FILE_A_REF,
        token_count=20,
        duplication_kind=DuplicationKind.INTERNAL,
    )
    assert pair.duplication_kind == DuplicationKind.INTERNAL
    assert pair.file_a_ref == pair.file_b_ref


def test_duplication_pair_line_ranges_and_similarity():
    pair = DuplicationPair(
        id="x",
        project_ref=PROJECT_REF,
        file_a_ref=FILE_A_REF,
        file_b_ref=FILE_B_REF,
        token_count=80,
        line_range_a=(10, 25),
        line_range_b=(100, 115),
        fingerprint="hash:abc",
        similarity_score=0.92,
    )
    assert pair.line_range_a == (10, 25)
    assert pair.fingerprint == "hash:abc"
    assert pair.similarity_score == pytest.approx(0.92)


def test_duplication_pair_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        DuplicationPair(
            id="x",
            project_ref=PROJECT_REF,
            file_a_ref=FILE_A_REF,
            file_b_ref=FILE_B_REF,
            token_count=10,
            file_a_path="x",  # legacy field — renamed to file_a_ref
        )


def test_duplication_pair_rejects_legacy_total_block_length():
    """Legacy ``total_block_length`` is renamed to ``token_count``."""
    with pytest.raises(ValidationError):
        DuplicationPair(
            id="x",
            project_ref=PROJECT_REF,
            file_a_ref=FILE_A_REF,
            file_b_ref=FILE_B_REF,
            total_block_length=10,  # legacy name — dropped
            token_count=10,
        )


def test_duplication_pair_rejects_python_object_file_refs():
    """Cross-entity refs must be EntityRef — never a Python object."""
    with pytest.raises(ValidationError):
        DuplicationPair(
            id="x",
            project_ref=PROJECT_REF,
            file_a_ref="not-a-ref",  # type: ignore[arg-type]
            file_b_ref=FILE_B_REF,
            token_count=10,
        )
