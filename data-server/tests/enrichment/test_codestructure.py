"""B2 JaFax — domain models, parser, relations, taggers, overview.

Verifies §10 of communication/B2_codeframe/index_step_general.md.

Synthetic fixture: a tiny JaFax layout (2 internal classes in 2 files, plus
one external library class) with one method-call edge, one field-access edge,
and one inheritance edge. The fixture is held in-memory (a list of dicts) so
the test does not depend on any on-disk layout file.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.codestructure_miner.linker.transformers import JaFaxTransformer
from src.codestructure_miner.parser import CodeStructureFormat, parse
from src.common.codestructure_models import CodeStructureProject
from src.common.models import GitProject
from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from src.enrichment.relations.calls import FileCallsExtractor
from src.enrichment.relations.coupling import FileCouplingExtractor
from src.enrichment.relations.data_access import FileDataAccessExtractor
from src.enrichment.relations.hierarchy import FileHierarchyExtractor
from src.enrichment.tagger.anomaly_coupling import PivotFileCouplingTagger
from src.enrichment.tagger.anomaly_timezone import TimezoneAnomalyTagger
from src.enrichment.tagger.base import TaggingContext
from tests.enrichment.fixtures import make_account, make_change, make_commit, make_file


UTC = timezone.utc


# ── Synthetic JaFax fixture ────────────────────────────────────────────────────

def _jafax_fixture() -> list[dict]:
    """Two internal classes in two files + one external dep, with edges:
       Foo.callsBar() -> Bar.work()       (method call)
       Foo.readsField() -> Bar.field      (field access)
       Bar extends Base                    (inheritance, Base is INTERNAL)
    """
    return [
        # Files
        {"type": "File", "id": 1,
         "name": "src/main/java/foo/Foo.java", "imports": []},
        {"type": "File", "id": 2,
         "name": "src/main/java/bar/Bar.java", "imports": []},
        {"type": "File", "id": 3,
         "name": "src/main/java/bar/Base.java", "imports": []},
        # Internal classes
        {"type": "Class", "id": 100, "name": "Foo", "pack": "foo",
         "fileName": "src/main/java/foo/Foo.java",
         "containedMethods": [200, 201], "containedFields": []},
        {"type": "Class", "id": 101, "name": "Bar", "pack": "bar",
         "fileName": "src/main/java/bar/Bar.java",
         "superClass": 102,
         "containedMethods": [202], "containedFields": [300]},
        {"type": "Class", "id": 102, "name": "Base", "pack": "bar",
         "fileName": "src/main/java/bar/Base.java",
         "containedMethods": [], "containedFields": []},
        # External library class — should be filtered out of edges.
        {"type": "Class", "id": 999, "name": "Object", "pack": "java.lang",
         "isExternal": True, "containedMethods": []},
        # Methods
        {"type": "Method", "id": 200, "name": "callsBar",
         "signature": "callsBar()", "container": 100,
         "calledMethods": [202, 999], "accessedFields": []},
        {"type": "Method", "id": 201, "name": "readsField",
         "signature": "readsField()", "container": 100,
         "calledMethods": [], "accessedFields": [300]},
        # Method without container — recovered via Class.containedMethods back-link.
        {"type": "Method", "id": 202, "name": "work",
         "signature": "work()", "calledMethods": [], "accessedFields": []},
        # Field
        {"type": "Attribute", "id": 300, "name": "field",
         "kind": "Field", "container": 101, "class": 102},
    ]


def _build_codestructure() -> CodeStructureProject:
    return JaFaxTransformer(_jafax_fixture()).transform()


# ── Parser & transformer tests ─────────────────────────────────────────────────

def test_jafax_transformer_basic_counts():
    cs = _build_codestructure()
    # External + type-parameter classes are dropped → 3 internal types.
    assert len(cs.type_registry.all) == 3
    # All three methods resolved (one via container, one via container, one via back-link).
    assert len(cs.method_registry.all) == 3
    # Single field with kind=Field.
    assert len(cs.field_registry.all) == 1


def test_jafax_transformer_method_back_link_recovers_container_none():
    cs = _build_codestructure()
    work = next(m for m in cs.method_registry.all if m.name == "work")
    # work() had container=None, yet should still resolve via Bar.containedMethods.
    assert work.file_path == "src/main/java/bar/Bar.java"
    assert work.parent_type_id == "jafax:101"


def test_jafax_transformer_external_targets_dropped():
    cs = _build_codestructure()
    # Foo.callsBar() called both Bar.work() and external Object.foo() — only the
    # internal call should survive in the reference registry.
    call_refs = [r for r in cs.reference_registry.all if r.kind == "call"]
    assert len(call_refs) == 1
    assert call_refs[0].from_file_path == "src/main/java/foo/Foo.java"
    assert call_refs[0].to_file_path == "src/main/java/bar/Bar.java"


def test_jafax_transformer_field_access_edge():
    cs = _build_codestructure()
    fa = [r for r in cs.reference_registry.all if r.kind == "fieldAccess"]
    assert len(fa) == 1
    # readsField() (in Foo.java) accesses Bar.field whose declaring type is Bar.
    assert fa[0].from_file_path == "src/main/java/foo/Foo.java"
    assert fa[0].to_file_path == "src/main/java/bar/Bar.java"


def test_jafax_transformer_inheritance_edge():
    cs = _build_codestructure()
    inh = [r for r in cs.reference_registry.all if r.kind == "inheritance"]
    assert len(inh) == 1
    assert inh[0].from_file_path == "src/main/java/bar/Bar.java"
    assert inh[0].to_file_path == "src/main/java/bar/Base.java"


def test_jafax_path_prefix_stripping():
    """Per orchestrator decision: configurable leading prefix stripped at parse time."""
    raw = [
        {"type": "Class", "id": 1, "name": "A", "pack": "x",
         "fileName": "zeppelin/src/main/java/A.java", "containedMethods": []},
    ]
    cs = JaFaxTransformer(raw, path_prefix="zeppelin").transform()
    t = next(iter(cs.type_registry.all))
    assert t.file_path == "src/main/java/A.java"


def test_parse_dispatches_to_jafax(tmp_path: Path):
    layout = tmp_path / "tiny-layout.json"
    layout.write_text(json.dumps(_jafax_fixture()))
    cs = parse(str(layout), CodeStructureFormat.JAFAX)
    assert len(cs.type_registry.all) == 3
    assert len(cs.method_registry.all) == 3


def test_parse_codeframe_not_implemented_yet(tmp_path: Path):
    """Per orchestrator decision: CodeFrame stub is intentionally absent."""
    import pytest
    p = tmp_path / "x.jsonl"
    p.write_text("")
    with pytest.raises(NotImplementedError):
        parse(str(p), CodeStructureFormat.CODEFRAME)


# ── Relation extractor tests ───────────────────────────────────────────────────

def _empty_ctx_with_codestructure(cs: CodeStructureProject) -> TaggingContext:
    return TaggingContext(
        graph_data={"git": None, "jira": None, "github": None,
                    "code_structure": cs, "metrics": {"lizard": []}},
        config=EnrichmentConfig(),
        anchor_date=None,
        recent_cutoff=None,
    )


def test_calls_extractor_aggregates_per_pair():
    cs = _build_codestructure()
    rels = FileCallsExtractor().extract(_empty_ctx_with_codestructure(cs))
    assert len(rels) == 1
    rf = rels[0]
    assert rf.kind == "calls.file-file"
    assert len(rf.relations) == 1
    assert rf.relations[0].source_id == "src/main/java/foo/Foo.java"
    assert rf.relations[0].target_id == "src/main/java/bar/Bar.java"
    assert rf.relations[0].strength == 1.0


def test_data_access_extractor():
    cs = _build_codestructure()
    rels = FileDataAccessExtractor().extract(_empty_ctx_with_codestructure(cs))
    assert len(rels) == 1
    assert rels[0].kind == "data-access.file-file"
    assert len(rels[0].relations) == 1


def test_hierarchy_extractor():
    cs = _build_codestructure()
    rels = FileHierarchyExtractor().extract(_empty_ctx_with_codestructure(cs))
    assert len(rels) == 1
    assert rels[0].kind == "hierarchy.file-file"
    assert len(rels[0].relations) == 1


def test_coupling_extractor_sums_kinds():
    cs = _build_codestructure()
    rels = FileCouplingExtractor().extract(_empty_ctx_with_codestructure(cs))
    assert len(rels) == 1
    rf = rels[0]
    # Foo -> Bar carries 1 call + 1 fieldAccess = 2; Bar -> Base carries 1
    # inheritance = 1. Two pairs total.
    assert len(rf.relations) == 2
    foo_to_bar = next(r for r in rf.relations
                      if r.source_id == "src/main/java/foo/Foo.java"
                      and r.target_id == "src/main/java/bar/Bar.java")
    assert foo_to_bar.strength == 2.0
    assert foo_to_bar.extras["breakdown"] == {"call": 1, "fieldAccess": 1}


def test_extractors_no_op_when_no_codestructure():
    ctx = TaggingContext(
        graph_data={"git": None, "jira": None, "github": None,
                    "code_structure": None, "metrics": {"lizard": []}},
        config=EnrichmentConfig(),
        anchor_date=None,
        recent_cutoff=None,
    )
    assert FileCallsExtractor().extract(ctx) == []
    assert FileCouplingExtractor().extract(ctx) == []


# ── Tagger tests ───────────────────────────────────────────────────────────────

def test_pivotfile_coupling_fires_above_threshold():
    """Build a hub file with 25 distinct callers (above MANY_PEERS=20)."""
    raw: list[dict] = []
    # Hub class + file
    raw.append({"type": "File", "id": 1, "name": "Hub.java", "imports": []})
    raw.append({"type": "Class", "id": 100, "name": "Hub", "pack": "h",
                "fileName": "Hub.java", "containedMethods": [200]})
    raw.append({"type": "Method", "id": 200, "name": "process",
                "signature": "process()", "container": 100,
                "calledMethods": [], "accessedFields": []})
    # 25 spoke classes, each with one method that calls Hub.process().
    for i in range(25):
        cls_id = 200 + i + 1
        method_id = 1000 + i
        raw.append({"type": "File", "id": 10 + i,
                    "name": f"Spoke{i}.java", "imports": []})
        raw.append({"type": "Class", "id": cls_id, "name": f"Spoke{i}", "pack": "s",
                    "fileName": f"Spoke{i}.java",
                    "containedMethods": [method_id]})
        raw.append({"type": "Method", "id": method_id, "name": "doIt",
                    "signature": "doIt()", "container": cls_id,
                    "calledMethods": [200], "accessedFields": []})

    cs = JaFaxTransformer(raw).transform()
    tagged = list(PivotFileCouplingTagger().tag(_empty_ctx_with_codestructure(cs)))
    hub_tags = next((t for t in tagged if t.entity_id == "Hub.java"), None)
    assert hub_tags is not None
    pivot = next(t for t in hub_tags.traits if t.name == "anomaly.structuring.PivotFile")
    assert pivot.evidence["basis"] == "coupling"
    assert pivot.evidence["coupling_degree"] == 25


def test_timezone_anomaly_zonecrossroad_fires():
    now = datetime.now(UTC)
    proj = GitProject(name="tz-test")
    alice = make_account("Alice", "a@x")
    proj.account_registry.add_all([alice])
    fobj = make_file(proj)
    proj.file_registry.add_all([fobj])

    utc = timezone.utc
    plus3 = timezone(timedelta(hours=3))

    for i in range(11):
        c = make_commit(proj, f"c_utc_{i}", "feat", alice,
                        now.astimezone(utc) - timedelta(days=200 - i))
        make_change(c, fobj, "src/multitime.py", added=5)
        proj.git_commit_registry.add(c)
    for i in range(11):
        # Use a later date to ensure they survive `recent_cutoff` filtering elsewhere.
        c = make_commit(proj, f"c_p3_{i}", "feat", alice,
                        now.astimezone(plus3) - timedelta(days=100 - i))
        make_change(c, fobj, "src/multitime.py", added=5)
        proj.git_commit_registry.add(c)

    g = {"git": proj, "jira": None, "github": None,
         "code_structure": None, "metrics": {"lizard": []}}
    e = compute_enrichments(g, EnrichmentConfig())
    tags = e.tags_by_entity.get("file:src/multitime.py")
    assert tags is not None
    zc = next((t for t in tags.traits if t.name == "anomaly.cohesion.ZoneCrossroad"), None)
    assert zc is not None
    assert zc.evidence["zones_with_activity"] >= 2


def test_feature_encapsulation_overview_present_without_jafax():
    """The overview must build on git-only projects (gracefully blank LOC column)."""
    now = datetime.now(UTC)
    proj = GitProject(name="fe-test")
    alice = make_account("Alice", "a@x")
    proj.account_registry.add_all([alice])
    fobj = make_file(proj)
    proj.file_registry.add_all([fobj])
    c = make_commit(proj, "c1", "feat", alice, now - timedelta(days=10))
    make_change(c, fobj, "src/foo.py", added=20)
    proj.git_commit_registry.add(c)

    g = {"git": proj, "jira": None, "github": None,
         "code_structure": None, "metrics": {"lizard": []}}
    e = compute_enrichments(g, EnrichmentConfig())
    table = e.overview("feature_encapsulation")
    assert table is not None
    assert "file_count" in table.columns
    assert "source_loc_kloc" in table.columns
    assert "wide_commit_pct" in table.columns
    project_row = next(r for r in table.rows if r.entity_id == "(project)")
    assert project_row.cells["file_count"].lifetime_value == 1
    assert project_row.cells["source_loc_kloc"].lifetime_value is None
