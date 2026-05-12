"""Lizard-metrics-domain registry tests."""
from __future__ import annotations

from pathlib import Path

from src.common.domains.metrics_lizard import (
    FileMetric,
    FileMetricRegistry,
    LizardMetricsProject,
    LizardMetricsProjectRegistry,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind
from src.common.pickle_store import PickleStore


PROJECT_ID = "lz-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)
FILE_A = EntityRef(kind=EntityKind.FILE, id="src/A.java")
FILE_B = EntityRef(kind=EntityKind.FILE, id="src/B.java")


def _metric(file_ref: EntityRef, name: str, value: float) -> FileMetric:
    return FileMetric(
        id=FileMetric.make_id(file_ref.id, name),
        project_ref=PROJECT_REF,
        file_ref=file_ref,
        metric_name=name,
        value=value,
    )


def test_lizard_metrics_project_registry_indexes():
    reg = LizardMetricsProjectRegistry()
    p = LizardMetricsProject(id="p1", name="X", source=SourceKind.LIZARD)
    reg.add(p)
    assert {q.id for q in reg.by_name["X"]} == {"p1"}


def test_file_metric_registry_indexes():
    reg = FileMetricRegistry()
    reg.add(_metric(FILE_A, "max_ccn", 12.0))
    reg.add(_metric(FILE_A, "sum_nloc", 240.0))
    reg.add(_metric(FILE_B, "max_ccn", 8.0))
    reg.add(_metric(FILE_B, "sum_nloc", 60.0))

    # by_file: all metrics for a single file
    assert {m.metric_name for m in reg.by_file[FILE_A]} == {"max_ccn", "sum_nloc"}
    assert {m.metric_name for m in reg.by_file[FILE_B]} == {"max_ccn", "sum_nloc"}
    # by_name: all values of a single metric across the project
    assert {m.value for m in reg.by_name["max_ccn"]} == {12.0, 8.0}
    assert {m.value for m in reg.by_name["sum_nloc"]} == {240.0, 60.0}
    assert {m.id for m in reg.by_project[PROJECT_REF]} == {
        FileMetric.make_id(FILE_A.id, "max_ccn"),
        FileMetric.make_id(FILE_A.id, "sum_nloc"),
        FileMetric.make_id(FILE_B.id, "max_ccn"),
        FileMetric.make_id(FILE_B.id, "sum_nloc"),
    }


def test_file_metric_registry_remove_updates_indexes():
    reg = FileMetricRegistry()
    m = _metric(FILE_A, "max_ccn", 12.0)
    reg.add(m)
    assert reg.by_name["max_ccn"] == (m,)
    reg.remove(m.id)
    assert reg.by_name["max_ccn"] == ()
    assert reg.by_file[FILE_A] == ()


def test_file_metric_registry_pickle_round_trip(tmp_path: Path):
    reg = FileMetricRegistry()
    reg.add(_metric(FILE_A, "max_ccn", 12.0))
    reg.add(_metric(FILE_B, "max_ccn", 8.0))
    store = PickleStore(tmp_path)
    store.write_registry(EntityKind.FILE_METRIC.value, reg)
    restored = store.read_registry(
        EntityKind.FILE_METRIC.value, FileMetricRegistry
    )
    assert len(restored) == 2
    assert {m.value for m in restored.by_name["max_ccn"]} == {12.0, 8.0}
