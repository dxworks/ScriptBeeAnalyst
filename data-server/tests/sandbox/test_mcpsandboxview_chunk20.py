"""``MCPSandboxView`` — Chunk 20 helpers backing the four formerly-404 MCP tools.

Each helper is exercised twice:

* Against a fixture :class:`Graph` carrying populated rows for the
  helper's domain — asserts the shape, the per-row fields, and the
  expected counts.
* Against an empty :class:`Graph` — asserts the empty-but-valid result
  (``count=0`` / ``loaded=False`` / ``projects=[]`` etc.) so the MCP
  tools degrade gracefully when no ingest happened for the relevant
  source.

See plan §1 D4 and §2 Chunk 20: every MCP tool POSTs to ``/execute``
with a one-liner on ``MCPSandboxView``; these tests pin the
``MCPSandboxView`` side of that contract.
"""
from __future__ import annotations

import pytest

from src.common.domains.code_structure.models import (
    CodeField,
    CodeMethod,
    CodeReference,
    CodeStructureProject,
    CodeType,
)
from src.common.domains.duplication.models import (
    DuplicationKind,
    DuplicationPair,
    DuplicationProject,
)
from src.common.domains.metrics_lizard.models import (
    FileMetric,
    LizardMetricsProject,
)
from src.common.kernel import EntityKind, EntityRef, Graph
from src.common.people import SourceKind
from src.sandbox import MCPSandboxView


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _empty_graph() -> Graph:
    return Graph(project_id="sb-empty")


def _file_ref(path: str) -> EntityRef:
    return EntityRef(kind=EntityKind.FILE, id=path)


# ----------------------------------------------------------------------
# Fixture — small but representative graph with code structure +
# duplication + lizard metrics.
# ----------------------------------------------------------------------
@pytest.fixture
def populated_graph() -> Graph:
    g = Graph(project_id="sb-c20")

    # --- Lizard metrics: 2 files, 4 rows each (full metric set) --------
    lz_project = LizardMetricsProject(
        id="lz-1", name="zep-lz", source=SourceKind.LIZARD
    )
    g.lizard_projects.add(lz_project)
    lz_ref = lz_project.ref()

    file_a = _file_ref("src/A.java")
    file_b = _file_ref("src/B.java")
    # File A — sizeable.
    for name, value in [
        ("sum_nloc", 240.0),
        ("max_ccn", 12.0),
        ("avg_ccn", 4.5),
        ("function_count", 14.0),
        ("longest_function_nloc", 38.0),
    ]:
        g.file_metrics.add(
            FileMetric(
                id=FileMetric.make_id(file_a.id, name),
                project_ref=lz_ref,
                file_ref=file_a,
                metric_name=name,
                value=value,
            )
        )
    # File B — smaller.
    for name, value in [
        ("sum_nloc", 60.0),
        ("max_ccn", 6.0),
        ("avg_ccn", 2.0),
        ("function_count", 5.0),
        ("longest_function_nloc", 14.0),
    ]:
        g.file_metrics.add(
            FileMetric(
                id=FileMetric.make_id(file_b.id, name),
                project_ref=lz_ref,
                file_ref=file_b,
                metric_name=name,
                value=value,
            )
        )

    # --- Code structure: 1 project, 2 types, 3 methods, 1 field, 2 refs.
    cs_project = CodeStructureProject(
        id="cs-1",
        name="zep-cs",
        source=SourceKind.CODE_STRUCTURE,
        kind_of_source="jafax",
    )
    g.code_structure_projects.add(cs_project)
    cs_ref = cs_project.ref()
    type_a = CodeType(
        id="jafax:1",
        project_ref=cs_ref,
        fully_qualified_name="com.zep.A",
        simple_name="A",
        type_category="class",
    )
    type_b = CodeType(
        id="jafax:2",
        project_ref=cs_ref,
        fully_qualified_name="com.zep.B",
        simple_name="B",
        type_category="class",
    )
    g.code_types.add(type_a)
    g.code_types.add(type_b)
    for mid in ("jafax:m1", "jafax:m2", "jafax:m3"):
        g.code_methods.add(
            CodeMethod(
                id=mid,
                project_ref=cs_ref,
                name=f"do_{mid[-1]}",
                type_ref=type_a.ref(),
            )
        )
    g.code_fields.add(
        CodeField(
            id="jafax:f1",
            project_ref=cs_ref,
            name="counter",
            type_ref=type_a.ref(),
        )
    )
    g.code_refs.add(
        CodeReference(
            id="jafax:r1",
            project_ref=cs_ref,
            reference_kind="call",
            source_method_ref=EntityRef(kind=EntityKind.CODE_METHOD, id="jafax:m1"),
            target_method_ref=EntityRef(kind=EntityKind.CODE_METHOD, id="jafax:m2"),
        )
    )
    g.code_refs.add(
        CodeReference(
            id="jafax:r2",
            project_ref=cs_ref,
            reference_kind="inheritance",
            source_type_ref=type_b.ref(),
            target_type_ref=type_a.ref(),
        )
    )

    # --- Duplication: 1 project, 3 pairs (external, sibling, internal).
    dup_project = DuplicationProject(
        id="dup-1", name="zep-dup", source=SourceKind.DUPLICATION
    )
    g.duplication_projects.add(dup_project)
    dup_ref = dup_project.ref()
    g.duplications.add(
        DuplicationPair(
            id=DuplicationPair.make_id("src/A.java", "src/C.java"),
            project_ref=dup_ref,
            file_a_ref=_file_ref("src/A.java"),
            file_b_ref=_file_ref("src/C.java"),
            token_count=180,
            duplication_kind=DuplicationKind.EXTERNAL,
        )
    )
    g.duplications.add(
        DuplicationPair(
            id=DuplicationPair.make_id("src/A.java", "src/B.java"),
            project_ref=dup_ref,
            file_a_ref=_file_ref("src/A.java"),
            file_b_ref=_file_ref("src/B.java"),
            token_count=40,
            duplication_kind=DuplicationKind.SIBLING,
        )
    )
    g.duplications.add(
        DuplicationPair(
            id=DuplicationPair.make_id("src/A.java", "src/A.java"),
            project_ref=dup_ref,
            file_a_ref=_file_ref("src/A.java"),
            file_b_ref=_file_ref("src/A.java"),
            token_count=20,
            duplication_kind=DuplicationKind.INTERNAL,
        )
    )

    return g


@pytest.fixture
def populated_view(populated_graph: Graph) -> MCPSandboxView:
    return MCPSandboxView(populated_graph)


@pytest.fixture
def empty_view() -> MCPSandboxView:
    return MCPSandboxView(_empty_graph())


# ----------------------------------------------------------------------
# list_metrics()
# ----------------------------------------------------------------------
def test_list_metrics_returns_catalog_dicts(populated_view: MCPSandboxView):
    """The Chunk-20 list_metrics returns a list of catalog dicts, not
    bare names, with each metric's name + family + emits_* shape."""
    catalog = populated_view.list_metrics()
    assert isinstance(catalog, list)
    # Chunk-7 ships ≥14 metrics; post-Chunk-12+ growth pushes higher.
    assert len(catalog) >= 1
    sample = catalog[0]
    assert {"name", "family", "emits_traits", "emits_classifiers",
            "emits_relations", "config_fields"}.issubset(sample.keys())
    assert isinstance(sample["name"], str) and sample["name"]
    assert isinstance(sample["emits_traits"], list)
    assert isinstance(sample["emits_classifiers"], list)


def test_list_metrics_on_empty_graph_still_returns_catalog(
    empty_view: MCPSandboxView,
):
    """The METRICS catalog is code-driven (not data-driven), so an
    empty graph still yields a populated catalog — the helper degrades
    to a list, never errors."""
    catalog = empty_view.list_metrics()
    assert isinstance(catalog, list)
    # Code-driven: 14 metrics ship by Chunk 7, more in later chunks.
    assert len(catalog) >= 1
    assert all(isinstance(entry, dict) for entry in catalog)


# ----------------------------------------------------------------------
# list_file_metrics()
# ----------------------------------------------------------------------
def test_list_file_metrics_returns_per_file_rollup(populated_view: MCPSandboxView):
    """Per-file Lizard rows are pivoted from `(file, metric_name)`
    rows into the legacy file-level shape, sorted by `sum_nloc` desc."""
    result = populated_view.list_file_metrics()
    assert result["count"] == 2
    files = result["files"]
    assert [f["file_path"] for f in files] == ["src/A.java", "src/B.java"]
    # File A is the larger one.
    a = files[0]
    assert a["source"] == "lizard"
    assert a["sum_nloc"] == 240.0
    assert a["max_ccn"] == 12.0
    assert a["avg_ccn"] == 4.5
    assert a["function_count"] == 14.0
    assert a["longest_function_nloc"] == 38.0


def test_list_file_metrics_min_loc_filters_small_files(
    populated_view: MCPSandboxView,
):
    """`min_loc` drops rows whose sum_nloc is below threshold."""
    result = populated_view.list_file_metrics(min_loc=100)
    # B has sum_nloc=60 — filtered out.
    assert result["count"] == 1
    assert result["files"][0]["file_path"] == "src/A.java"


def test_list_file_metrics_on_empty_graph(empty_view: MCPSandboxView):
    """No Lizard ingest → `{"count": 0, "files": []}`, never errors."""
    result = empty_view.list_file_metrics()
    assert result == {"count": 0, "files": []}


# ----------------------------------------------------------------------
# code_structure_summary()
# ----------------------------------------------------------------------
def test_code_structure_summary_per_project_counts(
    populated_view: MCPSandboxView,
):
    """One row per CodeStructureProject with the four sub-registry counts."""
    summary = populated_view.code_structure_summary()
    assert summary["loaded"] is True
    assert summary["source"] == "jafax"
    assert len(summary["projects"]) == 1
    row = summary["projects"][0]
    assert row["project_id"] == "cs-1"
    assert row["project_name"] == "zep-cs"
    assert row["kind_of_source"] == "jafax"
    assert row["type_count"] == 2
    assert row["method_count"] == 3
    assert row["field_count"] == 1
    assert row["ref_count"] == 2


def test_code_structure_summary_empty_graph(empty_view: MCPSandboxView):
    """No code-structure ingest → `loaded=False`, never errors."""
    summary = empty_view.code_structure_summary()
    assert summary == {"loaded": False, "source": None, "projects": []}


# ----------------------------------------------------------------------
# duplication_summary()
# ----------------------------------------------------------------------
def test_duplication_summary_bucket_counts(populated_view: MCPSandboxView):
    """Pairs are bucketed by DuplicationKind; totals add up."""
    summary = populated_view.duplication_summary()
    assert summary["loaded"] is True
    assert summary["source"] == "dude"
    assert len(summary["projects"]) == 1
    row = summary["projects"][0]
    assert row["project_id"] == "dup-1"
    assert row["external_pairs"] == 1
    assert row["sibling_pairs"] == 1
    assert row["internal_pairs"] == 1
    assert row["total_pairs"] == 3


def test_duplication_summary_empty_graph(empty_view: MCPSandboxView):
    """No DuDe ingest → `loaded=False`, never errors."""
    summary = empty_view.duplication_summary()
    assert summary == {"loaded": False, "source": None, "projects": []}
