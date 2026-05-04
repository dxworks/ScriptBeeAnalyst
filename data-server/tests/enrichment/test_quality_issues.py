"""B4 Insider — quality-issues models, parser, transformer, tagger, overview.

Verifies §10 of communication/B4_sonar_insider/index_step_general.md (with the
data-format corrections from index_step_data_format.md).

Synthetic fixture: a tiny Insider code-smells JSON with three records across
two categories and two files. The fixture exercises the loader end-to-end via
tmp_path so we hit the same code paths as a real run.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.common.models import GitProject
from src.common.quality_models import (
    QualityIssue,
    QualityIssueRegistry,
    QualityIssues,
)
from src.enrichment.config import EnrichmentConfig
from src.enrichment.overview.code_quality_table import CodeQualityOverview
from src.enrichment.pipeline import compute_enrichments
from src.enrichment.tagger.anomaly_quality_issues import (
    QualityIssuesTagger,
    _trait_name,
)
from src.enrichment.tagger.base import TaggingContext
from src.quality_miner.linker.transformers import (
    InsiderQualityIssuesTransformer,
)
from src.quality_miner.parser import (
    QualityIssueFormat,
    parse,
    parse_insider,
)
from src.quality_miner.reader_dto.loader import InsiderCodeSmellsJsonLoader
from src.quality_miner.reader_dto.models import InsiderCodeSmellRowDTO
from tests.enrichment.fixtures import (
    make_account,
    make_change,
    make_commit,
    make_file,
)


UTC = timezone.utc


# ── Fixtures ────────────────────────────────────────────────────────────────


def _write_insider_json(path: Path) -> Path:
    """3-record JSON with two distinct rules across two files / two categories."""
    path.write_text(json.dumps([
        {"name": "Stub Implementer", "category": "Inheritance",
         "file": "zeppelin/file/Foo.java", "value": 1},
        {"name": "Catch Top-Level Exception", "category": "Traceability",
         "file": "zeppelin/file/Foo.java", "value": 5},
        {"name": "Catch Top-Level Exception", "category": "Traceability",
         "file": "zeppelin/other/Bar.java", "value": 3},
    ]), encoding="utf-8")
    return path


def _empty_ctx_with_quality(qi: QualityIssues) -> TaggingContext:
    return TaggingContext(
        graph_data={"git": None, "jira": None, "github": None,
                    "code_structure": None, "duplication": None,
                    "metrics": {"lizard": []},
                    "quality_issues": qi},
        config=EnrichmentConfig(),
        anchor_date=None,
        recent_cutoff=None,
    )


# ── Loader / parser tests ──────────────────────────────────────────────────


def test_insider_loader_reads_4_field_records(tmp_path: Path):
    j = _write_insider_json(tmp_path / "z-code_smells.json")
    rows = InsiderCodeSmellsJsonLoader(str(j)).load()
    assert len(rows) == 3
    assert rows[0].name == "Stub Implementer"
    assert rows[0].category == "Inheritance"
    assert rows[0].file == "zeppelin/file/Foo.java"
    assert rows[0].value == 1


def test_insider_loader_skips_malformed_rows(tmp_path: Path):
    j = tmp_path / "broken.json"
    j.write_text(json.dumps([
        {"name": "A", "category": "Cat", "file": "f.java", "value": "not_int"},  # bad value
        {"name": "", "category": "Cat", "file": "f.java", "value": 1},  # empty name
        {"name": "B", "category": "", "file": "f.java", "value": 1},  # empty category
        {"name": "C", "category": "Cat", "file": "", "value": 1},  # empty file
        "not_an_object",  # not a dict
        {"name": "Good", "category": "Cat", "file": "f.java", "value": 7},  # one good
    ]), encoding="utf-8")
    rows = InsiderCodeSmellsJsonLoader(str(j)).load()
    assert len(rows) == 1
    assert rows[0].name == "Good"
    assert rows[0].value == 7


def test_insider_loader_rejects_non_list_root(tmp_path: Path):
    j = tmp_path / "obj.json"
    j.write_text(json.dumps({"records": []}), encoding="utf-8")
    try:
        InsiderCodeSmellsJsonLoader(str(j)).load()
    except ValueError as e:
        assert "list" in str(e).lower()
    else:
        raise AssertionError("expected ValueError on non-list root")


def test_parse_insider_end_to_end(tmp_path: Path):
    j = _write_insider_json(tmp_path / "z-code_smells.json")
    qi = parse_insider(str(j))
    assert qi.source == "insider"
    assert len(qi.issues) == 3
    # All three records preserved (one per record, no aggregation in parser).
    files = {i.file_path for i in qi.issues}
    assert files == {"zeppelin/file/Foo.java", "zeppelin/other/Bar.java"}
    # value -> occurrence_count, NOT severity
    foo_records = [i for i in qi.issues if i.file_path == "zeppelin/file/Foo.java"]
    assert sum(i.occurrence_count for i in foo_records) == 6  # 1 + 5


def test_parse_via_format_enum(tmp_path: Path):
    j = _write_insider_json(tmp_path / "x.json")
    qi = parse(str(j), QualityIssueFormat.INSIDER)
    assert len(qi.issues) == 3


def test_parse_sonarqube_raises_until_implemented():
    try:
        parse("/nonexistent.json", QualityIssueFormat.SONARQUBE)
    except NotImplementedError as e:
        assert "sonarqube" in str(e).lower() or "format" in str(e).lower()
    else:
        raise AssertionError("Sonar parser should raise NotImplementedError until it ships")


def test_parse_path_prefix_prepends(tmp_path: Path):
    j = tmp_path / "x.json"
    j.write_text(json.dumps([
        {"name": "Foo", "category": "C", "file": "src/A.java", "value": 1},
    ]), encoding="utf-8")
    qi = parse_insider(str(j), path_prefix="zeppelin")
    assert qi.issues[0].file_path == "zeppelin/src/A.java"


def test_parse_path_prefix_idempotent_when_already_prefixed(tmp_path: Path):
    j = tmp_path / "x.json"
    j.write_text(json.dumps([
        {"name": "Foo", "category": "C", "file": "zeppelin/src/A.java", "value": 1},
    ]), encoding="utf-8")
    qi = parse_insider(str(j), path_prefix="zeppelin")
    # Prefix not duplicated when input already carries it.
    assert not qi.issues[0].file_path.startswith("zeppelin/zeppelin/")
    assert qi.issues[0].file_path == "zeppelin/src/A.java"


# ── Transformer & ID stability ─────────────────────────────────────────────


def test_transformer_emits_stable_per_bin_ids():
    """Re-runs that reorder the JSON array must keep the same QualityIssue.id."""
    rows_a = [
        InsiderCodeSmellRowDTO(name="X", category="C", file="f.java", value=1),
        InsiderCodeSmellRowDTO(name="Y", category="C", file="f.java", value=2),
    ]
    rows_b = [
        InsiderCodeSmellRowDTO(name="Y", category="C", file="f.java", value=2),
        InsiderCodeSmellRowDTO(name="X", category="C", file="f.java", value=1),
    ]
    qi_a = InsiderQualityIssuesTransformer(rows_a).transform()
    qi_b = InsiderQualityIssuesTransformer(rows_b).transform()
    ids_a = {i.id for i in qi_a.issues}
    ids_b = {i.id for i in qi_b.issues}
    assert ids_a == ids_b


def test_transformer_indexes_collisions_per_bin():
    """Two records with same (file, rule) should get distinct ids."""
    rows = [
        InsiderCodeSmellRowDTO(name="Dup", category="C", file="f.java", value=1),
        InsiderCodeSmellRowDTO(name="Dup", category="C", file="f.java", value=2),
    ]
    qi = InsiderQualityIssuesTransformer(rows).transform()
    ids = [i.id for i in qi.issues]
    assert len(set(ids)) == 2  # distinct
    assert all(i.startswith("insider:f.java:Dup:") for i in ids)


# ── Trait-name builder ─────────────────────────────────────────────────────


def test_trait_name_strips_whitespace():
    assert _trait_name("Inheritance", "Stub Implementer") == \
        "anomaly.codesmell.Inheritance.StubImplementer"


def test_trait_name_strips_dots_in_segments():
    # Defensive: a category like "Code.Quality" must not break the dot-namespace.
    assert _trait_name("Code.Quality", "Foo Bar") == \
        "anomaly.codesmell.CodeQuality.FooBar"


# ── Tagger tests ───────────────────────────────────────────────────────────


def test_tagger_emits_one_trait_per_file_per_rule(tmp_path: Path):
    j = _write_insider_json(tmp_path / "z.json")
    qi = parse_insider(str(j))
    tagger = QualityIssuesTagger()
    out = list(tagger.tag(_empty_ctx_with_quality(qi)))
    by_fid = {tags.entity_id: tags for tags in out}
    # Foo.java has 2 rules, Bar.java has 1 rule.
    foo = by_fid["zeppelin/file/Foo.java"]
    assert len(foo.traits) == 2
    bar = by_fid["zeppelin/other/Bar.java"]
    assert len(bar.traits) == 1
    bar_trait = bar.traits[0]
    assert bar_trait.name == "anomaly.codesmell.Traceability.CatchTopLevelException"
    assert bar_trait.evidence["occurrence_count"] == 3
    assert bar_trait.evidence["basis"] == "insider"
    assert bar_trait.evidence["category"] == "Traceability"
    assert bar_trait.evidence["rule_name"] == "Catch Top-Level Exception"
    # severity == raw occurrence_count (no normalisation)
    assert bar_trait.severity == 3.0


def test_tagger_no_op_when_quality_issues_absent():
    ctx = TaggingContext(
        graph_data={"git": None, "jira": None, "github": None,
                    "code_structure": None, "duplication": None,
                    "metrics": {"lizard": []},
                    "quality_issues": None},
        config=EnrichmentConfig(),
        anchor_date=None,
        recent_cutoff=None,
    )
    assert list(QualityIssuesTagger().tag(ctx)) == []


def test_tagger_marks_git_match_in_evidence():
    """git_matched=True when the issue path matches a known git File id."""
    proj = GitProject(name="b4-insider")
    alice = make_account("Alice", "alice@example.com")
    proj.account_registry.add_all([alice])
    fobj = make_file(proj)
    proj.file_registry.add_all([fobj])
    now = datetime.now(UTC)
    c = make_commit(proj, "c0", "feat: add", alice, now - timedelta(days=10))
    make_change(c, fobj, "zeppelin/file/Foo.java", added=10)
    proj.git_commit_registry.add(c)

    qi = QualityIssues(source="insider", issues=[
        QualityIssue(
            id="insider:zeppelin/file/Foo.java:R:0",
            rule_name="R", category="C",
            file_path="zeppelin/file/Foo.java",
            occurrence_count=1,
        ),
        QualityIssue(
            id="insider:unknown.java:R:0",
            rule_name="R", category="C",
            file_path="unknown.java",
            occurrence_count=1,
        ),
    ])
    ctx = TaggingContext(
        graph_data={"git": proj, "jira": None, "github": None,
                    "code_structure": None, "duplication": None,
                    "metrics": {"lizard": []},
                    "quality_issues": qi},
        config=EnrichmentConfig(),
        anchor_date=None,
        recent_cutoff=None,
    )
    out = {t.entity_id: t for t in QualityIssuesTagger().tag(ctx)}
    assert out["zeppelin/file/Foo.java"].traits[0].evidence["git_matched"] is True
    assert out["unknown.java"].traits[0].evidence["git_matched"] is False


# ── Overview test ──────────────────────────────────────────────────────────


def test_code_quality_overview_aggregates_per_project(tmp_path: Path):
    j = _write_insider_json(tmp_path / "z.json")
    qi = parse_insider(str(j))
    # Build a synthetic git project with files matching the issue paths so the
    # ComponentResolver bins them; otherwise CodeQualityOverview only emits
    # the (project) row and per-component rows have no data.
    proj = GitProject(name="b4-overview")
    alice = make_account("Alice", "alice@example.com")
    proj.account_registry.add_all([alice])
    f1 = make_file(proj)
    f2 = make_file(proj)
    proj.file_registry.add_all([f1, f2])
    now = datetime.now(UTC)
    c1 = make_commit(proj, "c1", "feat: add foo", alice, now - timedelta(days=10))
    make_change(c1, f1, "zeppelin/file/Foo.java", added=10)
    c2 = make_commit(proj, "c2", "feat: add bar", alice, now - timedelta(days=10))
    make_change(c2, f2, "zeppelin/other/Bar.java", added=10)
    proj.git_commit_registry.add_all([c1, c2])

    graph = {
        "git": proj, "jira": None, "github": None,
        "code_structure": None, "duplication": None,
        "metrics": {"lizard": []},
        "quality_issues": qi,
    }
    e = compute_enrichments(graph, EnrichmentConfig())
    table = e.overview("code_quality")
    assert table is not None
    assert "total_smells" in table.columns
    assert "distinct_rules" in table.columns
    assert "top_rule" in table.columns
    project_row = next(r for r in table.rows if r.entity_id == "(project)")
    # 1 + 5 + 3 = 9 occurrences across 2 distinct rules across 2 files.
    assert project_row.cells["total_smells"].lifetime_value == 9
    assert project_row.cells["distinct_rules"].lifetime_value == 2
    assert project_row.cells["distinct_files"].lifetime_value == 2
    # Top rule by aggregate occurrence: Catch Top-Level Exception (5+3=8)
    assert project_row.cells["top_rule"].lifetime_value == "Catch Top-Level Exception"
    assert project_row.cells["top_rule_count"].lifetime_value == 8
    assert project_row.cells["top_rule_2"].lifetime_value == "Stub Implementer"
    assert project_row.cells["top_rule_2_count"].lifetime_value == 1


def test_code_quality_overview_empty_when_no_quality_issues():
    proj = GitProject(name="b4-empty")
    graph = {
        "git": proj, "jira": None, "github": None,
        "code_structure": None, "duplication": None,
        "metrics": {"lizard": []},
        "quality_issues": None,
    }
    e = compute_enrichments(graph, EnrichmentConfig())
    table = e.overview("code_quality")
    assert table is not None
    assert table.rows == []  # no quality_issues = empty rows list


# ── End-to-end pipeline test ───────────────────────────────────────────────


def test_pipeline_emits_codesmell_traits_through_full_run(tmp_path: Path):
    """Run the full enrichment pipeline and verify codesmell.* traits appear."""
    j = _write_insider_json(tmp_path / "z.json")
    qi = parse_insider(str(j))
    proj = GitProject(name="b4-pipeline")
    alice = make_account("Alice", "alice@example.com")
    proj.account_registry.add_all([alice])
    fobj = make_file(proj)
    proj.file_registry.add_all([fobj])
    now = datetime.now(UTC)
    c = make_commit(proj, "c0", "feat: add", alice, now - timedelta(days=10))
    make_change(c, fobj, "zeppelin/file/Foo.java", added=10)
    proj.git_commit_registry.add(c)

    graph = {
        "git": proj, "jira": None, "github": None,
        "code_structure": None, "duplication": None,
        "metrics": {"lizard": []},
        "quality_issues": qi,
    }
    e = compute_enrichments(graph, EnrichmentConfig())
    foo_tags = e.tags_by_entity.get("file:zeppelin/file/Foo.java")
    assert foo_tags is not None
    trait_names = {t.name for t in foo_tags.traits}
    assert "anomaly.codesmell.Inheritance.StubImplementer" in trait_names
    assert "anomaly.codesmell.Traceability.CatchTopLevelException" in trait_names


# ── Registry contract ──────────────────────────────────────────────────────


def test_quality_issue_registry_keys_by_id():
    reg = QualityIssueRegistry()
    issue = QualityIssue(
        id="insider:f.java:R:0",
        rule_name="R", category="C",
        file_path="f.java", occurrence_count=1,
    )
    reg.add(issue)
    assert reg.get_by_id("insider:f.java:R:0") is issue
