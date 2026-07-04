"""Quality-domain entity construction tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.common.domains.quality import (
    QualityIssue,
    QualityProject,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


PROJECT_ID = "q-proj-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)
FILE_REF = EntityRef(kind=EntityKind.FILE, id="src/Foo.java")


def test_quality_project_construct_defaults_to_insider():
    p = QualityProject(
        id=PROJECT_ID, name="ZEPPELIN", source=SourceKind.QUALITY
    )
    assert p.kind == EntityKind.PROJECT
    assert p.source == SourceKind.QUALITY
    assert p.source_tool == "insider"  # default


def test_quality_project_accepts_sonar():
    p = QualityProject(
        id=PROJECT_ID,
        name="z",
        source=SourceKind.QUALITY,
        source_tool="sonar",
    )
    assert p.source_tool == "sonar"


def test_quality_project_rejects_unknown_source_tool():
    with pytest.raises(ValidationError):
        QualityProject(
            id=PROJECT_ID,
            name="z",
            source=SourceKind.QUALITY,
            source_tool="checkstyle",  # not in Literal
        )


def test_quality_project_transformer_class():
    from src.common.domains.quality.transformer import QualityTransformer

    p = QualityProject(id=PROJECT_ID, name="z", source=SourceKind.QUALITY)
    assert p.transformer_class() is QualityTransformer


def test_quality_project_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        QualityProject(
            id=PROJECT_ID,
            name="z",
            source=SourceKind.QUALITY,
            mystery=1,  # type: ignore[call-arg]
        )


def test_quality_issue_construct_insider_shape():
    issue = QualityIssue(
        id="insider:src/Foo.java:Stub Implementer:0",
        project_ref=PROJECT_REF,
        file_ref=FILE_REF,
        rule_id="Stub Implementer",
        category="Inheritance",
        occurrence_count=5,
    )
    assert issue.kind == EntityKind.QUALITY_ISSUE
    assert issue.rule_id == "Stub Implementer"
    assert issue.category == "Inheritance"
    assert issue.occurrence_count == 5
    assert issue.source_tool == "insider"  # default
    assert issue.severity is None
    assert issue.line_start is None


def test_quality_issue_construct_sonar_shape():
    issue = QualityIssue(
        id="sonar:abc123",
        project_ref=PROJECT_REF,
        file_ref=FILE_REF,
        rule_id="squid:S1192",
        category="bug",
        severity="CRITICAL",
        message="Define a constant for this literal.",
        line_start=42,
        line_end=42,
        language="java",
        source_tool="sonar",
    )
    assert issue.source_tool == "sonar"
    assert issue.severity == "CRITICAL"
    assert issue.message == "Define a constant for this literal."
    assert issue.line_start == 42


def test_quality_issue_rejects_legacy_rule_name():
    """Legacy ``rule_name`` is renamed to ``rule_id`` in v2."""
    with pytest.raises(ValidationError):
        QualityIssue(
            id="x",
            project_ref=PROJECT_REF,
            file_ref=FILE_REF,
            rule_name="X",  # legacy field — renamed to rule_id
            category="c",
        )


def test_quality_issue_rejects_legacy_file_path():
    """Legacy ``file_path: str`` is replaced by ``file_ref: EntityRef``."""
    with pytest.raises(ValidationError):
        QualityIssue(
            id="x",
            project_ref=PROJECT_REF,
            file_path="src/Foo.java",  # legacy
            rule_id="X",
            category="c",
        )


def test_quality_issue_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        QualityIssue(
            id="x",
            project_ref=PROJECT_REF,
            file_ref=FILE_REF,
            rule_id="X",
            category="c",
            line_number=10,  # legacy field — split into line_start/line_end
        )


def test_quality_issue_rejects_unknown_source_tool():
    with pytest.raises(ValidationError):
        QualityIssue(
            id="x",
            project_ref=PROJECT_REF,
            file_ref=FILE_REF,
            rule_id="X",
            category="c",
            source_tool="pmd",  # not in Literal
        )
