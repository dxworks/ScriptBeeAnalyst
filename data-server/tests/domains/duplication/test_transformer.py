"""Duplication-domain Transformer tests."""
from __future__ import annotations

import pytest

from src.common.domains import Transformer, TransformResult
from src.common.domains.duplication import (
    DuplicationKind,
    DuplicationPair,
    DuplicationProject,
    DuplicationTransformer,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "dup-1"


def _build_entity_bundle() -> dict:
    project = DuplicationProject(
        id=PROJECT_ID, name="ZEPPELIN", source=SourceKind.DUPLICATION
    )
    project_ref = project.ref()
    file_a = EntityRef(kind=EntityKind.FILE, id="src/A.java")
    file_b = EntityRef(kind=EntityKind.FILE, id="src/B.java")
    pair = DuplicationPair(
        id=DuplicationPair.make_id(file_a.id, file_b.id),
        project_ref=project_ref,
        file_a_ref=file_a,
        file_b_ref=file_b,
        token_count=80,
        duplication_kind=DuplicationKind.EXTERNAL,
    )
    return {"project": project, "duplication_pairs": [pair]}


def test_duplication_transformer_is_a_transformer():
    assert issubclass(DuplicationTransformer, Transformer)
    assert DuplicationTransformer.source == SourceKind.DUPLICATION


def test_duplication_transformer_happy_path():
    bundle = _build_entity_bundle()
    result = DuplicationTransformer().transform(bundle)
    assert isinstance(result, TransformResult)
    assert result.project is bundle["project"]
    assert set(result.entities) == {EntityKind.DUPLICATION_PAIR}
    assert len(result.entities[EntityKind.DUPLICATION_PAIR]) == 1


def test_duplication_transformer_handles_missing_optional_buckets():
    project = DuplicationProject(
        id=PROJECT_ID, name="z", source=SourceKind.DUPLICATION
    )
    result = DuplicationTransformer().transform({"project": project})
    assert result.entities[EntityKind.DUPLICATION_PAIR] == []


def test_duplication_transformer_rejects_missing_project_key():
    with pytest.raises(ValueError, match="'project'"):
        DuplicationTransformer().transform({"duplication_pairs": []})


def test_duplication_transformer_rejects_wrong_project_type():
    with pytest.raises(TypeError, match="DuplicationProject"):
        DuplicationTransformer().transform({"project": "not-a-project"})


def test_duplication_transformer_rejects_wrong_entity_in_bucket():
    bundle = _build_entity_bundle()
    bundle["duplication_pairs"] = [bundle["project"]]  # project in pairs bucket
    with pytest.raises(TypeError, match="duplication_pairs"):
        DuplicationTransformer().transform(bundle)


def test_duplication_transformer_rejects_unknown_bundle_keys():
    bundle = _build_entity_bundle()
    bundle["files"] = []  # git bucket leaked
    with pytest.raises(ValueError, match="unknown bundle keys"):
        DuplicationTransformer().transform(bundle)


def test_duplication_transformer_rejects_raw_dto_for_now():
    with pytest.raises(NotImplementedError, match="entity-bundle"):
        DuplicationTransformer().transform(object())
