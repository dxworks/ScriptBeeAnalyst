"""Lizard-metrics-domain entities for the v2 graph.

Faithful port of ``src/common/lizard_models.py`` (legacy Lizard CSV
ingest). Every cross-entity reference uses :class:`EntityRef`, never a
Python object reference — per plan §4.

Entity-vs-value-object decisions (plan §1.1):

* :class:`LizardMetricsProject` is a real :class:`Project` subclass.
* :class:`FileMetric` is a real :class:`Entity` subclass
  (``EntityKind.FILE_METRIC`` is in the kernel enum set). Per the Chunk-6
  brief: **one entity per (file, metric_name) pair**, NOT one entity per
  file carrying a ``metrics: dict[str, float]`` blob. This makes the
  registry queryable / indexable by metric name and matches the
  per-entity-shape discipline the v2 graph follows everywhere else.
* :class:`FunctionMetric` is kept as a frozen value object. The legacy
  model carried a per-file list of :class:`FunctionMetric`; v2 keeps it
  as a value-object list attached to a FileMetric row that represents
  the file-functions rollup (see :pyattr:`FileMetric.functions`).
  **Why not a separate Entity**: the kernel ``EntityKind`` enum does not
  list ``FUNCTION_METRIC``, and no downstream consumer (Chunks 7/8 + the
  MCP sandbox helpers) addresses individual functions today — they
  query at the file or rollup level. Re-promoting :class:`FunctionMetric`
  to an Entity later is purely additive (the frozen model already has
  the right shape minus ``id`` + ``kind``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, List, Optional

from pydantic import BaseModel, ConfigDict

from ...kernel import Entity, EntityKind, EntityRef
from ...projects import Project

if TYPE_CHECKING:  # forward-only — keeps cycles broken
    from .transformer import LizardMetricsTransformer  # noqa: F401


# ---------------------------------------------------------------------------
# Value objects (NOT entities)
# ---------------------------------------------------------------------------


class FunctionMetric(BaseModel):
    """A single function's complexity metrics as Lizard reports it.

    Value object — no ``FUNCTION_METRIC`` member exists in
    :class:`EntityKind` and no downstream consumer indexes them across
    files. Nested inside :class:`FileMetric.functions` on the
    file-functions rollup row.

    Frozen + ``extra="forbid"`` so cycle-free pickle stays trivial.

    Field mapping vs legacy ``lizard_models.FunctionMetric``:

    * ``name``, ``long_name``, ``class_name``, ``nloc``,
      ``cyclomatic_complexity``, ``parameters``, ``token_count``,
      ``length``, ``start_line``, ``end_line`` — all preserved
      identically.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    long_name: str
    nloc: int
    cyclomatic_complexity: int
    parameters: int
    token_count: int
    length: int
    start_line: int
    end_line: int
    class_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class LizardMetricsProject(Project):
    """A single Lizard metrics project's metadata.

    Per plan §3 the Project is metadata only — entities live in flat
    registries on :class:`Graph`. The legacy ``FileMetric`` model was
    file-scoped with all metrics on it; v2 splits per (file, metric_name)
    so the registry can be indexed by metric name.
    """

    kind: ClassVar[EntityKind] = EntityKind.PROJECT

    def transformer_class(self) -> type["LizardMetricsTransformer"]:  # type: ignore[override]
        from .transformer import LizardMetricsTransformer

        return LizardMetricsTransformer


class FileMetric(Entity):
    """A single scalar metric measured at the file level.

    One entity per ``(file, metric_name)`` pair. Same file with three
    different metrics → three entities. This shape:

    * makes the registry's ``by_name`` index actually useful (you can ask
      "all CCN values across the project"),
    * stays consistent with the v2 "one entity per addressable thing"
      discipline,
    * means a future per-function detail row can sit on the same
      registry without a shape change (``metric_name`` carries the
      semantic).

    Field mapping vs legacy ``lizard_models.FileMetric``:

    * ``id``                  — was an auto-generated UUID. v2 uses a
                                composite id ``"{file_path}#{metric_name}"``
                                via :meth:`FileMetric.make_id`. Stable
                                across re-ingests (no UUID churn).
    * ``project_ref``         — NEW: typed ref to
                                :class:`LizardMetricsProject`.
    * ``file_ref``            — was ``file_path`` (a plain string); v2
                                carries the typed :class:`EntityRef` to
                                :class:`git.File`.
    * ``metric_name``         — NEW (plan §4); names the metric this row
                                carries (``"sum_nloc"`` / ``"max_ccn"`` /
                                ``"avg_ccn"`` / ``"function_count"`` /
                                ``"longest_function_nloc"`` /
                                ``"token_count"`` / …). Identifies the
                                row uniquely with ``file_ref``.
    * ``value``               — was the per-metric scalar fields on the
                                legacy ``FileMetric`` (``sum_nloc`` /
                                ``max_ccn`` / ``avg_ccn`` /
                                ``function_count`` /
                                ``longest_function_nloc``). v2 collapses
                                them into a single ``value: float``; the
                                row's identity comes from
                                ``metric_name``.
    * ``source``              — was ``source: str``; preserved so a row
                                knows which tool produced it (Lizard
                                today, but a future complexity tool
                                could co-exist).
    * ``functions``           — was ``functions: List[FunctionMetric]``.
                                Preserved but only meaningful on
                                "rollup" rows — by convention the
                                transformer attaches the per-function
                                list to the ``"function_count"`` row.
                                Other metric rows leave it empty (the
                                default ``[]``). Documented in handoff.

    Why ``value: float`` (not ``int`` for counts)? Lizard's ``avg_ccn``
    is a float; integer metrics fit safely into a float (Lizard's
    counts are well within float precision range). Single type keeps
    the registry uniform.
    """

    kind: ClassVar[EntityKind] = EntityKind.FILE_METRIC

    project_ref: EntityRef
    file_ref: EntityRef
    metric_name: str
    value: float
    source: str = "lizard"
    functions: List[FunctionMetric] = []

    @staticmethod
    def make_id(file_path: str, metric_name: str) -> str:
        """Composite registry id ``"{file_path}#{metric_name}"``.

        Used at the Chunk-8 builder boundary; the entity layer accepts
        any string id but the canonical form keeps cross-process re-runs
        stable.
        """
        return f"{file_path}#{metric_name}"


__all__ = [
    "FileMetric",
    "FunctionMetric",
    "LizardMetricsProject",
]
