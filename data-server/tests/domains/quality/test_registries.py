"""Quality-domain registry tests."""
from __future__ import annotations

from pathlib import Path

from src.common.domains.quality import (
    QualityIssue,
    QualityIssueRegistry,
    QualityProject,
    QualityProjectRegistry,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind
from src.common.pickle_store import PickleStore


PROJECT_ID = "q-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)
FILE_A = EntityRef(kind=EntityKind.FILE, id="src/A.java")
FILE_B = EntityRef(kind=EntityKind.FILE, id="src/B.java")


def _issue(
    iid: str,
    file_ref: EntityRef,
    rule_id: str,
    category: str,
    *,
    severity: str | None = None,
    source_tool: str = "insider",
) -> QualityIssue:
    return QualityIssue(
        id=iid,
        project_ref=PROJECT_REF,
        file_ref=file_ref,
        rule_id=rule_id,
        category=category,
        severity=severity,
        source_tool=source_tool,
    )


# ---------------------------------------------------------------------------
# QualityProjectRegistry
# ---------------------------------------------------------------------------


def test_quality_project_registry_indexes():
    reg = QualityProjectRegistry()
    p_ins = QualityProject(
        id="p1", name="X", source=SourceKind.QUALITY, source_tool="insider"
    )
    p_sonar = QualityProject(
        id="p2", name="X", source=SourceKind.QUALITY, source_tool="sonar"
    )
    reg.add(p_ins)
    reg.add(p_sonar)
    assert {p.id for p in reg.by_name["X"]} == {"p1", "p2"}
    assert {p.id for p in reg.by_source_tool["insider"]} == {"p1"}
    assert {p.id for p in reg.by_source_tool["sonar"]} == {"p2"}


# ---------------------------------------------------------------------------
# QualityIssueRegistry
# ---------------------------------------------------------------------------


def test_quality_issue_registry_indexes():
    reg = QualityIssueRegistry()
    reg.add(_issue("1", FILE_A, "Stub Implementer", "Inheritance"))
    reg.add(_issue("2", FILE_A, "Other", "Inheritance"))
    reg.add(
        _issue(
            "3",
            FILE_B,
            "squid:S1192",
            "bug",
            severity="CRITICAL",
            source_tool="sonar",
        )
    )
    reg.add(
        _issue(
            "4",
            FILE_B,
            "Stub Implementer",
            "Inheritance",
            severity="MAJOR",
            source_tool="sonar",
        )
    )

    assert {i.id for i in reg.by_file[FILE_A]} == {"1", "2"}
    assert {i.id for i in reg.by_file[FILE_B]} == {"3", "4"}
    assert {i.id for i in reg.by_rule_id["Stub Implementer"]} == {"1", "4"}
    assert {i.id for i in reg.by_category["Inheritance"]} == {"1", "2", "4"}
    assert {i.id for i in reg.by_category["bug"]} == {"3"}
    # by_severity skips None (Insider issues without severity)
    assert {i.id for i in reg.by_severity["CRITICAL"]} == {"3"}
    assert {i.id for i in reg.by_severity["MAJOR"]} == {"4"}
    assert reg.by_severity[None] == ()
    assert {i.id for i in reg.by_project[PROJECT_REF]} == {"1", "2", "3", "4"}


def test_quality_issue_registry_remove_updates_indexes():
    reg = QualityIssueRegistry()
    issue = _issue("1", FILE_A, "X", "c")
    reg.add(issue)
    assert reg.by_file[FILE_A] == (issue,)
    reg.remove(issue.id)
    assert reg.by_file[FILE_A] == ()
    assert reg.by_rule_id["X"] == ()


def test_quality_issue_registry_pickle_round_trip(tmp_path: Path):
    reg = QualityIssueRegistry()
    reg.add(_issue("1", FILE_A, "X", "c", severity="CRITICAL"))
    reg.add(_issue("2", FILE_B, "Y", "c"))
    store = PickleStore(tmp_path)
    store.write_registry(EntityKind.QUALITY_ISSUE.value, reg)
    restored = store.read_registry(
        EntityKind.QUALITY_ISSUE.value, QualityIssueRegistry
    )
    assert len(restored) == 2
    assert {i.id for i in restored.by_severity["CRITICAL"]} == {"1"}
    assert restored.by_severity[None] == ()
